from flask import (Blueprint, render_template,
                   session, redirect, url_for,
                   request, jsonify)
from functools import wraps
from models import db, AuditLog, ApprovalRequest
from app.vm.azure_service import get_all_vms
from app.vm.disk_service import (
    get_vm_disks,
    get_available_disks
)
from app.vm.snapshot_service import get_snapshots
from sqlalchemy import func

admin_bp = Blueprint(
    'admin',
    __name__,
    url_prefix='/admin',
    template_folder='templates'
)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login',
                                    next=request.url))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@admin_bp.route('/dashboard')
@login_required
def dashboard():
    user = session.get('user')

    try:
        vms   = get_all_vms()
        error = None
    except Exception as e:
        vms   = []
        error = str(e)

    total_vms   = len(vms)
    running_vms = len([
        v for v in vms
        if v.get('power_state') == 'Running'
    ])
    stopped_vms = total_vms - running_vms

    pending_approvals = ApprovalRequest.query.filter_by(
        status='pending'
    ).count()

    recent_logs = AuditLog.query.order_by(
        AuditLog.timestamp.desc()
    ).limit(10).all()

    actions_by_type = db.session.query(
        AuditLog.action,
        func.count(AuditLog.id).label('count')
    ).group_by(AuditLog.action).all()

    actions_by_day = db.session.query(
        func.date(AuditLog.timestamp).label('date'),
        func.count(AuditLog.id).label('count')
    ).group_by(
        func.date(AuditLog.timestamp)
    ).order_by(
        func.date(AuditLog.timestamp).desc()
    ).limit(7).all()

    return render_template(
        'admin/dashboard.html',
        user=user,
        vms=vms,
        error=error,
        total_vms=total_vms,
        running_vms=running_vms,
        stopped_vms=stopped_vms,
        pending_approvals=pending_approvals,
        recent_logs=recent_logs,
        actions_by_type=actions_by_type,
        actions_by_day=actions_by_day
    )


@admin_bp.route('/vms')
@login_required
def vms():
    user = session.get('user')

    try:
        vms   = get_all_vms()
        error = None
    except Exception as e:
        vms   = []
        error = str(e)

    return render_template(
        'admin/vms.html',
        user=user,
        vms=vms,
        error=error
    )


@admin_bp.route('/vms/<vm_name>/detail')
@login_required
def vm_detail(vm_name):
    user           = session.get('user')
    resource_group = request.args.get('rg')

    try:
        disks           = get_vm_disks(
                              resource_group, vm_name
                          )
        snapshots       = get_snapshots(resource_group)
        available_disks = get_available_disks(
                              resource_group
                          )
        error           = None
    except Exception as e:
        disks           = []
        snapshots       = []
        available_disks = []
        error           = str(e)

    return render_template(
        'admin/vm_detail.html',
        user=user,
        vm_name=vm_name,
        resource_group=resource_group,
        disks=disks,
        snapshots=snapshots,
        available_disks=available_disks,
        error=error
    )


@admin_bp.route('/approvals')
@login_required
def approvals():
    user       = session.get('user')
    user_email = user.get('preferred_username')

    pending = ApprovalRequest.query.filter_by(
        status='pending'
    ).order_by(
        ApprovalRequest.created_at.desc()
    ).all()

    to_approve = ApprovalRequest.query.filter(
        ApprovalRequest.status == 'pending'
    ).filter(
        (ApprovalRequest.approver1_azure == user_email) |
        (ApprovalRequest.approver2_azure == user_email)
    ).all()

    my_requests = ApprovalRequest.query.filter_by(
        requester_email=user_email
    ).order_by(
        ApprovalRequest.created_at.desc()
    ).all()

    return render_template(
        'admin/approvals.html',
        user=user,
        pending=pending,
        to_approve=to_approve,
        my_requests=my_requests
    )


@admin_bp.route('/audit')
@login_required
def audit():
    user = session.get('user')
    return render_template(
        'admin/audit.html',
        user=user
    )
