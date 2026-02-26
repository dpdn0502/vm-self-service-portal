# Flask imports
from flask import (Blueprint, render_template, session,
                   redirect, url_for, request, jsonify,
                   Response)
from app.vm.rbac_service import (
    require_permission,
    has_permission,
    get_session_role
)

# Standard library imports
import csv
import io
from functools import wraps
from datetime import datetime

# Database models
from models import db, AuditLog, ApprovalRequest

# Azure services
from app.vm.azure_service import (
    get_all_vms, start_vm, stop_vm, restart_vm, resize_vm
)
from app.vm.disk_service import (
    get_vm_disks,
    get_available_disks,
    attach_disk,
    detach_disk
)
from app.vm.snapshot_service import (
    create_snapshot,
    get_snapshots,
    delete_snapshot
)

# ServiceNow
from app.servicenow.snow_service import create_incident

# Blueprint — MUST be after all imports
vm_bp = Blueprint('vm', __name__, url_prefix='/vm')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login',
                                    next=request.url))
        return f(*args, **kwargs)
    return decorated


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

    print(f"─── VM ACTION ──────────────────────────")
    print(f"User:   {user.get('preferred_username')}")
    print(f"VM:     {vm_name}")
    print(f"Action: {action}")
    print(f"────────────────────────────────────────")
    
   
    # ── Permission Check ──────────────────────────────────────
    if action in ['start', 'stop', 'restart'] and \
       not has_permission('start'):
        return jsonify({
            'status':  'error',
            'message': 'Access denied — '
                       'Operator role required to '
                       'perform this action'
        })

    if action == 'resize' and \
       not has_permission('resize'):
        return jsonify({
            'status':  'error',
            'message': 'Access denied — '
                       'Contributor role required '
                       'to resize VMs'
        })
    # ─────────────────────────────────────────────────────────

    try:
        if action == 'start':
            message = start_vm(resource_group, vm_name)
        elif action == 'stop':
            message = stop_vm(resource_group, vm_name)
        elif action == 'restart':
            message = restart_vm(resource_group, vm_name)
        else:
            return jsonify({
                'status':  'error',
                'message': f'Unknown action: {action}'
            })

        status = 'success'

    except Exception as e:
        message = str(e)
        status  = 'error'
        print(f"❌ VM action failed: {e}")
    
    try:
        if action == 'start':
            message = start_vm(resource_group, vm_name)
        elif action == 'stop':
            message = stop_vm(resource_group, vm_name)
        elif action == 'restart':
            message = restart_vm(resource_group, vm_name)
        else:
            return jsonify({
                'status':  'error',
                'message': f'Unknown action: {action}'
            })

        status = 'success'

    except Exception as e:
        message = str(e)
        status  = 'error'
        print(f"❌ VM action failed: {e}")

    # Create ServiceNow ticket
    snow_result = create_incident(
        vm_name        = vm_name,
        action         = action,
        user_name      = user.get('name'),
        user_email     = user.get('preferred_username'),
        resource_group = resource_group,
        status         = status,
        message        = message
    )

    # Log to audit
    log = AuditLog(
        user_email      = user.get('preferred_username'),
        user_name       = user.get('name'),
        vm_name         = vm_name,
        resource_group  = resource_group,
        action          = action,
        status          = status,
        message         = message,
        snow_ticket     = snow_result.get('incident_number'),
        snow_ticket_url = snow_result.get('incident_url')
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        'status':      status,
        'message':     message,
        'snow_ticket': snow_result.get('incident_number'),
        'snow_url':    snow_result.get('incident_url')
    })


@vm_bp.route('/whoami')
@login_required
def whoami():
    user = session.get('user')
    return jsonify(user)


@vm_bp.route('/audit')
@login_required
def audit():
    user = session.get('user')
    return render_template(
        'audit.html',
        user=user
    )


@vm_bp.route('/audit/data')
@login_required
def audit_data():
    """
    Returns filtered audit data as JSON
    Used by frontend for dynamic filtering
    """
    date_from     = request.args.get('date_from', '')
    date_to       = request.args.get('date_to', '')
    vm_filter     = request.args.get('vm', '')
    user_filter   = request.args.get('user', '')
    action_filter = request.args.get('action', '')

    query = AuditLog.query

    if date_from:
        query = query.filter(
            AuditLog.timestamp >= date_from
        )
    if date_to:
        query = query.filter(
            AuditLog.timestamp <= date_to + ' 23:59:59'
        )
    if vm_filter:
        query = query.filter(
            AuditLog.vm_name.ilike(f'%{vm_filter}%')
        )
    if user_filter:
        query = query.filter(
            AuditLog.user_email.ilike(f'%{user_filter}%')
        )
    if action_filter:
        query = query.filter(
            AuditLog.action == action_filter
        )

    logs = query.order_by(
        AuditLog.timestamp.desc()
    ).all()

    return jsonify({
        'logs':  [log.to_dict() for log in logs],
        'total': len(logs)
    })


@vm_bp.route('/audit/stats')
@login_required
def audit_stats():
    """Returns statistics for charts"""
    from sqlalchemy import func

    # Actions by type
    actions_by_type = db.session.query(
        AuditLog.action,
        func.count(AuditLog.id).label('count')
    ).group_by(AuditLog.action).all()

    # Actions by status
    actions_by_status = db.session.query(
        AuditLog.status,
        func.count(AuditLog.id).label('count')
    ).group_by(AuditLog.status).all()

    # Actions by user
    actions_by_user = db.session.query(
        AuditLog.user_name,
        func.count(AuditLog.id).label('count')
    ).group_by(AuditLog.user_name).order_by(
        func.count(AuditLog.id).desc()
    ).limit(5).all()

    # Actions by day last 7 days
    actions_by_day = db.session.query(
        func.date(AuditLog.timestamp).label('date'),
        func.count(AuditLog.id).label('count')
    ).group_by(
        func.date(AuditLog.timestamp)
    ).order_by(
        func.date(AuditLog.timestamp).desc()
    ).limit(7).all()

    # Totals
    total_actions = AuditLog.query.count()
    total_success = AuditLog.query.filter_by(
        status='success'
    ).count()
    total_failed  = AuditLog.query.filter_by(
        status='error'
    ).count()

    return jsonify({
        'actions_by_type': [
            {'action': r.action, 'count': r.count}
            for r in actions_by_type
        ],
        'actions_by_status': [
            {'status': r.status, 'count': r.count}
            for r in actions_by_status
        ],
        'actions_by_user': [
            {'user': r.user_name, 'count': r.count}
            for r in actions_by_user
        ],
        'actions_by_day': [
            {'date': str(r.date), 'count': r.count}
            for r in actions_by_day
        ],
        'totals': {
            'total':   total_actions,
            'success': total_success,
            'failed':  total_failed
        }
    })


@vm_bp.route('/audit/export')
@login_required
def export_audit():
    """Export audit log to CSV"""
    logs = AuditLog.query.order_by(
        AuditLog.timestamp.desc()
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        'Timestamp',
        'User Name',
        'User Email',
        'VM Name',
        'Resource Group',
        'Action',
        'Status',
        'Message',
        'ServiceNow Ticket'
    ])

    # Data rows
    for log in logs:
        writer.writerow([
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            log.user_name,
            log.user_email,
            log.vm_name,
            log.resource_group,
            log.action,
            log.status,
            log.message,
            log.snow_ticket or ''
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition':
                'attachment; filename=vm-audit-log.csv'
        }
    )


@vm_bp.route('/<vm_name>/detail')
@login_required
def vm_detail(vm_name):
    """VM detail page showing disks and snapshots"""
    user           = session.get('user')
    resource_group = request.args.get('rg')

    print(f"─── VM DETAIL ──────────────────────────")
    print(f"VM:             {vm_name}")
    print(f"Resource Group: {resource_group}")
    print(f"────────────────────────────────────────")

    try:
        disks           = get_vm_disks(resource_group, vm_name)
        snapshots       = get_snapshots(resource_group)
        available_disks = get_available_disks(resource_group)
        error           = None
    except Exception as e:
        import traceback
        print(f"❌ VM detail error: {e}")
        print(traceback.format_exc())
        disks           = []
        snapshots       = []
        available_disks = []
        error           = str(e)

    return render_template(
        'vm_detail.html',
        user=user,
        vm_name=vm_name,
        resource_group=resource_group,
        disks=disks,
        snapshots=snapshots,
        available_disks=available_disks,
        error=error
    )


@vm_bp.route('/disk/action', methods=['POST'])
@login_required
def disk_action():
    """Handles disk attach/detach via approval workflow"""
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')
    disk_name      = request.form.get('disk_name')
    disk_id        = request.form.get('disk_id', '')

    print(f"─── DISK ACTION ────────────────────────")
    print(f"User:   {user.get('preferred_username')}")
    print(f"VM:     {vm_name}")
    print(f"Action: {action}")
    print(f"Disk:   {disk_name}")
    print(f"────────────────────────────────────────")

    action_details = (
        f"Disk: {disk_name} | "
        f"Action: {action.upper()} | "
        f"VM: {vm_name} | "
        f"DiskID: {disk_id}"
    )

    from app.approvals.approval_service import (
        create_approval_request
    )
    approval = create_approval_request(
        user, vm_name, resource_group,
        action, action_details
    )

    return jsonify({
        'success':     True,
        'approval_id': approval.id,
        'snow_ticket': approval.snow_ticket,
        'message':     (
            f"Approval request #{approval.id} submitted "
            f"for disk {action}. "
            f"Ticket: {approval.snow_ticket or 'Pending'}"
        )
    })


@vm_bp.route('/snapshot/action', methods=['POST'])
@login_required
def snapshot_action():
    """Handles snapshot create/delete via approval workflow"""
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')
    disk_name      = request.form.get('disk_name', '')
    snapshot_name  = request.form.get('snapshot_name', '')

    print(f"─── SNAPSHOT ACTION ────────────────────")
    print(f"User:     {user.get('preferred_username')}")
    print(f"VM:       {vm_name}")
    print(f"Action:   {action}")
    print(f"Disk:     {disk_name}")
    print(f"Snapshot: {snapshot_name}")
    print(f"────────────────────────────────────────")

    action_details = (
        f"Action: {action.upper()} | "
        f"Disk: {disk_name} | "
        f"Snapshot: {snapshot_name} | "
        f"VM: {vm_name}"
    )

    from app.approvals.approval_service import (
        create_approval_request
    )
    approval = create_approval_request(
        user, vm_name, resource_group,
        action, action_details
    )

    return jsonify({
        'success':     True,
        'approval_id': approval.id,
        'snow_ticket': approval.snow_ticket,
        'message':     (
            f"Approval request #{approval.id} submitted "
            f"for snapshot {action}. "
            f"Ticket: {approval.snow_ticket or 'Pending'}"
        )
    })