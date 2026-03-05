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
    get_all_vms, get_vms_for_user,
    start_vm, stop_vm, restart_vm, resize_vm,
    get_vm_info, invalidate_vm_cache
)
from app.vm.tag_service import get_vm_tags, STANDARD_TAGS
from app.vm.timezone_service import get_common_timezones
from app.vm.dns_service import get_vm_dns_config
from app.vm.patch_service import (
    get_vm_patch_status,
    get_patch_classifications,
    REBOOT_OPTIONS,
)
from app.vm.metrics_service import get_vm_metrics
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
    user        = session.get('user')
    portal_role = session.get('portal_role', 'reader')
    user_oid    = user.get('oid', '')
    # Detect dev test-login (no real Azure OID)
    is_test     = session.get('access_token') == 'test-token'

    try:
        vms   = get_vms_for_user(user_oid, portal_role, is_test)
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

    # Bust VM list cache so the next page load shows updated state
    if status == 'success':
        invalidate_vm_cache()

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
        vm_info         = get_vm_info(resource_group, vm_name)
        # Fetch current VM tags for the read-only tags panel
        vm_tags         = get_vm_tags(resource_group, vm_name)
        error           = None
    except Exception as e:
        import traceback
        print(f"❌ VM detail error: {e}")
        print(traceback.format_exc())
        disks           = []
        snapshots       = []
        available_disks = []
        vm_info         = {
            'os_type':        'Unknown',
            'admin_username': None,
            'public_ip':      None
        }
        vm_tags         = {}
        error           = str(e)

    # DNS config fetched separately — page still loads if this fails
    try:
        dns_config = get_vm_dns_config(resource_group, vm_name)
    except Exception as e:
        print(f"⚠️ DNS config fetch failed: {e}")
        dns_config = {
            'os_type':  vm_info.get('os_type', 'Unknown'),
            'hostname': vm_name,
            'nics':     []
        }

    # Patch config fetched separately — page still loads if this fails
    try:
        patch_config = get_vm_patch_status(
            resource_group, vm_name
        )
    except Exception as e:
        print(f"⚠️ Patch config fetch failed: {e}")
        patch_config = {
            'vm_name':        vm_name,
            'os_type':        vm_info.get('os_type', 'Unknown'),
            'patch_mode':     'Unknown',
            'assessment_mode': 'Unknown',
            'aum_enabled':    False,
            'auto_assess':    False,
            'ring':           None,
            'status':         'error',
            'status_label':   'Error fetching patch config',
            'windows_classifications': [],
            'linux_classifications':   [],
            'reboot_options': REBOOT_OPTIONS,
            'error':          str(e),
        }

    return render_template(
        'vm_detail.html',
        user=user,
        vm_name=vm_name,
        resource_group=resource_group,
        disks=disks,
        snapshots=snapshots,
        available_disks=available_disks,
        vm_info=vm_info,
        vm_tags=vm_tags,
        standard_tags=STANDARD_TAGS,
        timezones=get_common_timezones(),
        dns_config=dns_config,
        patch_config=patch_config,
        error=error
    )


@vm_bp.route('/patch/action', methods=['POST'])
@login_required
def patch_action():
    """Submit a patch action for approval — Operator+ only"""
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')

    if not has_permission('start'):
        return jsonify({
            'success': False,
            'message': 'Access denied — Operator role required'
        })

    if action == 'patch_assess':
        action_details = (
            f"VM: {vm_name} | "
            f"Action: Trigger patch assessment"
        )
        label = 'patch assessment trigger'

    elif action == 'patch_install':
        os_type         = request.form.get('os_type', 'Linux')
        class_raw       = request.form.get('classifications', '')
        reboot_setting  = request.form.get(
            'reboot_setting', 'IfRequired'
        )
        classifications = [
            c.strip()
            for c in class_raw.split(',')
            if c.strip()
        ] or (
            ['Critical', 'Security']
            if os_type == 'Windows'
            else ['Critical', 'Security']
        )
        action_details = (
            f"OS: {os_type} | "
            f"Classifications: {','.join(classifications)} | "
            f"Reboot: {reboot_setting}"
        )
        label = (
            f"patch install ({', '.join(classifications)}) "
            f"reboot={reboot_setting}"
        )

    elif action == 'patch_mode_set':
        os_type    = request.form.get('os_type', 'Linux')
        patch_mode = request.form.get(
            'patch_mode', 'AutomaticByPlatform'
        )
        action_details = (
            f"OS: {os_type} | "
            f"Mode: {patch_mode}"
        )
        label = f"patch mode set to '{patch_mode}'"

    elif action == 'patch_reboot':
        action_details = (
            f"VM: {vm_name} | "
            f"Action: Reboot for pending patches"
        )
        label = 'reboot for pending patches'

    else:
        return jsonify({
            'success': False,
            'message': f'Unknown patch action: {action}'
        })

    from app.approvals.approval_service import create_approval_request
    approval = create_approval_request(
        user, vm_name, resource_group, action, action_details
    )

    return jsonify({
        'success':     True,
        'approval_id': approval.id,
        'snow_ticket': approval.snow_ticket,
        'message':     (
            f"Approval #{approval.id} submitted for {label}. "
            f"Ticket: {approval.snow_ticket or 'Pending'}"
        )
    })


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


@vm_bp.route('/tag/action', methods=['POST'])
@login_required
def tag_action():
    """Submits a tag update or delete for approval — Contributor+ only"""
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')   # tag_update | tag_delete
    tag_key        = request.form.get('tag_key', '').strip()
    tag_value      = request.form.get('tag_value', '').strip()

    # Block read-only roles from submitting tag changes
    if not has_permission('edit_tags'):
        return jsonify({
            'status':  'error',
            'message': 'Access denied — Contributor role required'
                       ' to modify tags'
        })

    # Build detail string in Key: X | Value: Y format for executor
    if action == 'tag_update':
        action_details = (
            f"Key: {tag_key} | Value: {tag_value} | VM: {vm_name}"
        )
    else:
        action_details = f"Key: {tag_key} | VM: {vm_name}"

    from app.approvals.approval_service import create_approval_request
    approval = create_approval_request(
        user, vm_name, resource_group, action, action_details
    )

    return jsonify({
        'success':     True,
        'approval_id': approval.id,
        'snow_ticket': approval.snow_ticket,
        'message':     (
            f"Approval #{approval.id} submitted for tag "
            f"{'update' if action == 'tag_update' else 'deletion'}. "
            f"Ticket: {approval.snow_ticket or 'Pending'}"
        )
    })


@vm_bp.route('/timezone/action', methods=['POST'])
@login_required
def timezone_action():
    """Submit a timezone change for approval — Operator+ only"""
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    os_type        = request.form.get('os_type')
    timezone_id    = request.form.get('timezone_id', '').strip()
    timezone_label = request.form.get('timezone_label', '').strip()

    if not has_permission('start'):
        return jsonify({
            'status':  'error',
            'message': 'Access denied — Operator role required'
                       ' to change timezone'
        })

    if not timezone_id:
        return jsonify({
            'status':  'error',
            'message': 'No timezone selected'
        })

    action_details = (
        f"OS: {os_type} | "
        f"Timezone: {timezone_id} | "
        f"Label: {timezone_label}"
    )

    from app.approvals.approval_service import create_approval_request
    approval = create_approval_request(
        user, vm_name, resource_group,
        'timezone_change', action_details
    )

    return jsonify({
        'success':     True,
        'approval_id': approval.id,
        'snow_ticket': approval.snow_ticket,
        'message':     (
            f"Approval #{approval.id} submitted to set timezone "
            f"to '{timezone_label}'. "
            f"Ticket: {approval.snow_ticket or 'Pending'}"
        )
    })


@vm_bp.route('/dns/action', methods=['POST'])
@login_required
def dns_action():
    """Submit a DNS change for approval — Operator+ only"""
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')  # dns_hostname_change | dns_server_update | dns_suffix_change

    if not has_permission('start'):
        return jsonify({
            'success': False,
            'message': 'Access denied — Operator role required'
        })

    if action == 'dns_hostname_change':
        os_type      = request.form.get('os_type', 'Linux')
        new_hostname = request.form.get('new_hostname', '').strip()
        if not new_hostname:
            return jsonify({'success': False,
                            'message': 'Hostname cannot be empty'})
        action_details = (
            f"OS: {os_type} | Hostname: {new_hostname}"
        )
        label = f"hostname change to '{new_hostname}'"

    elif action == 'dns_server_update':
        nic_name    = request.form.get('nic_name', '').strip()
        nic_rg      = request.form.get('nic_rg', resource_group).strip()
        servers_raw = request.form.get('dns_servers', '').strip()
        # Normalise: split on newlines or commas, drop blanks
        dns_servers = [
            s.strip()
            for s in servers_raw.replace('\n', ',').split(',')
            if s.strip()
        ]
        servers_csv = ', '.join(dns_servers) if dns_servers \
                      else 'Azure Default'
        action_details = (
            f"NIC: {nic_name} | "
            f"NIC_RG: {nic_rg} | "
            f"Servers: {','.join(dns_servers)}"
        )
        label = f"DNS server update on NIC '{nic_name}' → {servers_csv}"

    elif action == 'dns_suffix_change':
        os_type      = request.form.get('os_type', 'Linux')
        suffixes_raw = request.form.get('dns_suffixes', '').strip()
        suffixes     = [
            s.strip()
            for s in suffixes_raw.replace('\n', ',').split(',')
            if s.strip()
        ]
        if not suffixes:
            return jsonify({'success': False,
                            'message': 'Enter at least one search suffix'})
        action_details = (
            f"OS: {os_type} | "
            f"Suffixes: {','.join(suffixes)}"
        )
        label = f"DNS suffix update → {', '.join(suffixes)}"

    else:
        return jsonify({'success': False,
                        'message': f'Unknown DNS action: {action}'})

    from app.approvals.approval_service import create_approval_request
    approval = create_approval_request(
        user, vm_name, resource_group, action, action_details
    )

    return jsonify({
        'success':     True,
        'approval_id': approval.id,
        'snow_ticket': approval.snow_ticket,
        'message':     (
            f"Approval #{approval.id} submitted for {label}. "
            f"Ticket: {approval.snow_ticket or 'Pending'}"
        )
    })

# ── VM Performance Metrics ────────────────────────────────────

@vm_bp.route('/metrics/<vm_name>')
@login_required
def vm_metrics(vm_name):
    """Return Azure Monitor metrics as JSON for Chart.js."""
    rg    = request.args.get('rg', '')
    hours = request.args.get('hours', 1, type=int)
    hours = max(1, min(hours, 24))

    data = get_vm_metrics(rg, vm_name, hours)
    return jsonify(data)
