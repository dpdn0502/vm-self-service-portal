from flask import (Blueprint, render_template, session,
                   redirect, url_for, request, jsonify)
from app.vm.azure_service import get_all_vms, start_vm, stop_vm, restart_vm
from app.servicenow.snow_service import create_incident, test_connection
from models import db, AuditLog
from functools import wraps

vm_bp = Blueprint('vm', __name__, url_prefix='/vm')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def log_action(user, vm_name, resource_group, action,
               status, message, snow_ticket=None, snow_url=None):
    """Save every VM action to audit database"""
    log = AuditLog(
        user_email      = user.get('preferred_username'),
        user_name       = user.get('name'),
        vm_name         = vm_name,
        resource_group  = resource_group,
        action          = action,
        status          = status,
        message         = message,
        subscription    = 'dev-subscription',
        snow_ticket     = snow_ticket,
        snow_ticket_url = snow_url
    )
    db.session.add(log)
    db.session.commit()


@vm_bp.route('/dashboard')
@login_required
def dashboard():
    user = session.get('user')

    try:
        vms   = get_all_vms()
        error = None
    except Exception as e:
        vms   = []
        error = str(e)

    recent_logs = AuditLog.query.order_by(
        AuditLog.timestamp.desc()
    ).limit(10).all()

    return render_template(
        'dashboard.html',
        user=user,
        vms=vms,
        error=error,
        recent_logs=recent_logs
    )


@vm_bp.route('/action', methods=['POST'])
@login_required
def vm_action():
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')

    try:
        # Step 1 — Execute Azure action
        if action == 'start':
            message = start_vm(resource_group, vm_name)
        elif action == 'stop':
            message = stop_vm(resource_group, vm_name)
        elif action == 'restart':
            message = restart_vm(resource_group, vm_name)
        else:
            return jsonify({
                'status':  'error',
                'message': 'Unknown action'
            }), 400

        # Step 2 — Create ServiceNow incident
        snow_result = create_incident(
            vm_name        = vm_name,
            action         = action,
            user_name      = user.get('name'),
            user_email     = user.get('preferred_username'),
            resource_group = resource_group,
            status         = 'success',
            message        = message
        )

        # DEBUG — remove this line once working
        print(f"ServiceNow result: {snow_result}")

        # Extract ticket info
        snow_ticket = None
        snow_url    = None
        snow_msg    = ''

        if snow_result['success']:
            snow_ticket = snow_result['incident_number']
            snow_url    = snow_result['incident_url']
            snow_msg    = f" | 🎫 Ticket: {snow_ticket}"
        else:
            snow_msg    = f" | ⚠️ Snow: {snow_result['error']}"

        # Step 3 — Log to audit database
        log_action(
            user, vm_name, resource_group,
            action, 'success', message,
            snow_ticket, snow_url
        )

        return jsonify({
            'status':      'success',
            'message':     message + snow_msg,
            'snow_ticket': snow_ticket,
            'snow_url':    snow_url
        })

    except Exception as e:
        error_msg = str(e)

        # Log failed action to ServiceNow too
        create_incident(
            vm_name        = vm_name,
            action         = action,
            user_name      = user.get('name'),
            user_email     = user.get('preferred_username'),
            resource_group = resource_group,
            status         = 'failed',
            message        = error_msg
        )

        log_action(
            user, vm_name, resource_group,
            action, 'failed', error_msg
        )

        return jsonify({
            'status':  'error',
            'message': error_msg
        }), 500


@vm_bp.route('/snow/test')
@login_required
def test_snow():
    """Quick endpoint to test ServiceNow connectivity"""
    result = test_connection()
    return jsonify(result)


@vm_bp.route('/audit')
@login_required
def audit_log():
    user = session.get('user')
    logs = AuditLog.query.order_by(
        AuditLog.timestamp.desc()
    ).all()
    return render_template('audit.html', user=user, logs=logs)