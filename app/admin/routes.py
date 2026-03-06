from flask import (Blueprint, render_template,
                   session, redirect, url_for,
                   request, jsonify)
from functools import wraps
from models import db, AuditLog, ApprovalRequest, DecommissionRequest
from app.vm.azure_service import get_all_vms, get_vm_info
from app.vm.tag_service import (
    get_vm_tags,
    get_all_vms_with_tags,
    STANDARD_TAGS
)
from app.vm.disk_service import (
    get_vm_disks,
    get_available_disks
)
from app.vm.snapshot_service import get_snapshots
from app.vm.timezone_service import get_common_timezones
from app.vm.dns_service import get_vm_dns_config
from app.vm.patch_service import (
    get_vm_patch_status,
    get_all_vms_patch_summary,
    REBOOT_OPTIONS,
)
from app.vm.metrics_service import get_vm_metrics
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
    import re
    from collections import OrderedDict

    user          = session.get('user')
    status_filter = request.args.get('status', '').lower()

    try:
        all_vms = get_all_vms()
        error   = None
    except Exception as e:
        all_vms = []
        error   = str(e)

    # Apply status filter
    if status_filter == 'running':
        filtered = [v for v in all_vms
                    if v.get('power_state') == 'Running']
    elif status_filter == 'stopped':
        filtered = [v for v in all_vms
                    if v.get('power_state') != 'Running']
    else:
        filtered      = all_vms
        status_filter = ''

    # Build Sub → RG grouped tree for filtered view
    def _safe(s):
        return re.sub(r'[^a-z0-9]+', '-',
                      str(s).lower()).strip('-') or 'unknown'

    grouped = OrderedDict()
    for vm in filtered:
        sub = vm.get('subscription_name', 'Unknown Subscription')
        rg  = vm.get('resource_group', 'Unknown RG')
        if sub not in grouped:
            grouped[sub] = {'id': _safe(sub), 'rgs': OrderedDict()}
        rgs = grouped[sub]['rgs']
        if rg not in rgs:
            rgs[rg] = {'id': _safe(sub + '-' + rg), 'vms': []}
        rgs[rg]['vms'].append(vm)

    return render_template(
        'admin/vms.html',
        user=user,
        vms=filtered,
        error=error,
        status_filter=status_filter,
        grouped=grouped
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
        vm_info         = get_vm_info(resource_group, vm_name)
        # Fetch current tags for the tags panel
        vm_tags         = get_vm_tags(resource_group, vm_name)
        error           = None
    except Exception as e:
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
        print(f"[WARN] DNS config fetch failed: {e}")
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
        print(f"[WARN] Patch config fetch failed: {e}")
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
        'admin/vm_detail.html',
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


@admin_bp.route('/approvals')
@login_required
def approvals():
    user       = session.get('user')
    user_email = user.get('preferred_username')

    portal_role = session.get('portal_role')

    pending = ApprovalRequest.query.filter_by(
        status='pending'
    ).order_by(
        ApprovalRequest.created_at.desc()
    ).all()

    # Admins see all pending requests and can decide any of them
    if portal_role == 'admin':
        to_approve = pending
    else:
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


# ── Tag Management Routes ──────────────────────────────────────

@admin_bp.route('/tags')
@login_required
def tags():
    """Tags overview — lists all VMs with their current tags"""
    user = session.get('user')

    try:
        # Fetch all VMs including their tag dicts in one API call
        vms   = get_all_vms_with_tags()
        error = None
    except Exception as e:
        vms   = []
        error = str(e)

    return render_template(
        'admin/tags.html',
        user          = user,
        vms           = vms,
        standard_tags = STANDARD_TAGS,
        error         = error
    )


@admin_bp.route('/tags/<vm_name>/data')
@login_required
def tags_data(vm_name):
    """AJAX endpoint — returns current tags for a specific VM as JSON"""
    resource_group = request.args.get('rg')

    try:
        tags = get_vm_tags(resource_group, vm_name)
        return jsonify({'success': True, 'tags': tags})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@admin_bp.route('/tags/action', methods=['POST'])
@login_required
def tag_action():
    """Submits a tag update, bulk update, or delete request for approval"""
    import json as _json
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')    # tag_update | tag_bulk_update | tag_delete

    print(f"─── TAG ACTION ─────────────────────────")
    print(f"User:   {user.get('preferred_username')}")
    print(f"VM:     {vm_name}")
    print(f"Action: {action}")
    print(f"────────────────────────────────────────")

    if action == 'tag_bulk_update':
        # tags_json is a JSON string: {"Key": "Value", ...}
        tags_json = request.form.get('tags_json', '{}')
        try:
            tags_dict = _json.loads(tags_json)
        except ValueError:
            return jsonify({'success': False,
                            'message': 'Invalid tags JSON'})

        if not tags_dict:
            return jsonify({'success': False,
                            'message': 'No tags provided'})

        # Store the raw JSON as action_details so executor can parse it
        action_details = _json.dumps(tags_dict)
        label          = f"{len(tags_dict)} tag(s)"

    elif action == 'tag_update':
        tag_key   = request.form.get('tag_key', '').strip()
        tag_value = request.form.get('tag_value', '').strip()
        action_details = (
            f"Key: {tag_key} | Value: {tag_value} | VM: {vm_name}"
        )
        label = f"tag update ({tag_key})"

    else:  # tag_delete
        tag_key        = request.form.get('tag_key', '').strip()
        action_details = f"Key: {tag_key} | VM: {vm_name}"
        label          = f"tag deletion ({tag_key})"

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


@admin_bp.route('/timezone/action', methods=['POST'])
@login_required
def timezone_action():
    """Submit a timezone change for approval — Operator+ only"""
    from app.vm.rbac_service import has_permission
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    os_type        = request.form.get('os_type')
    timezone_id    = request.form.get('timezone_id', '').strip()
    timezone_label = request.form.get('timezone_label', '').strip()

    if not has_permission('start'):
        return jsonify({
            'success': False,
            'message': 'Access denied — Operator role required'
                       ' to change timezone'
        })

    if not timezone_id:
        return jsonify({
            'success': False,
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


@admin_bp.route('/dns/action', methods=['POST'])
@login_required
def dns_action():
    """Submit a DNS change for approval — Operator+ only"""
    from app.vm.rbac_service import has_permission
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')

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
        action_details = f"OS: {os_type} | Hostname: {new_hostname}"
        label = f"hostname change to '{new_hostname}'"

    elif action == 'dns_server_update':
        nic_name    = request.form.get('nic_name', '').strip()
        nic_rg      = request.form.get('nic_rg',
                          resource_group).strip()
        servers_raw = request.form.get('dns_servers', '').strip()
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
            f"OS: {os_type} | Suffixes: {','.join(suffixes)}"
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


# ── Patch Management Routes ────────────────────────────────────

@admin_bp.route('/patches')
@login_required
def patches():
    """Patch compliance dashboard — all VMs with patch config"""
    import re
    from collections import OrderedDict

    user = session.get('user')

    try:
        vms   = get_all_vms_patch_summary()
        error = None
    except Exception as e:
        vms   = []
        error = str(e)

    aum_enabled_count = sum(
        1 for v in vms if v.get('aum_enabled')
    )
    aum_not_enabled   = len(vms) - aum_enabled_count
    auto_assess_count = sum(
        1 for v in vms if v.get('auto_assess')
    )

    rings = {}
    for v in vms:
        r = v.get('ring') or 'Unassigned'
        rings[r] = rings.get(r, 0) + 1

    # Build Subscription → RG grouped tree
    def _safe(s):
        return re.sub(r'[^a-z0-9]+', '-',
                      str(s).lower()).strip('-') or 'x'

    grouped = OrderedDict()
    for vm in vms:
        sub = vm.get('subscription_id', 'unknown')
        rg  = vm.get('resource_group', 'Unknown')
        if sub not in grouped:
            grouped[sub] = {
                'id':  _safe(sub),
                'rgs': OrderedDict()
            }
        rgs = grouped[sub]['rgs']
        if rg not in rgs:
            rgs[rg] = {
                'id':  _safe(sub + '-' + rg),
                'vms': []
            }
        rgs[rg]['vms'].append(vm)

    return render_template(
        'admin/patches.html',
        user              = user,
        vms               = vms,
        error             = error,
        aum_enabled_count = aum_enabled_count,
        aum_not_enabled   = aum_not_enabled,
        auto_assess_count = auto_assess_count,
        rings             = rings,
        total_vms         = len(vms),
        grouped           = grouped,
    )


@admin_bp.route('/patch/action', methods=['POST'])
@login_required
def patch_action():
    """Submit a patch action for approval — Operator+ only"""
    from app.vm.rbac_service import has_permission
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
        ] or ['Critical', 'Security']
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


# ── VM Performance Metrics ────────────────────────────────────

@admin_bp.route('/metrics/<vm_name>')
@login_required
def vm_metrics(vm_name):
    """Return Azure Monitor metrics as JSON for Chart.js."""
    rg    = request.args.get('rg', '')
    hours = request.args.get('hours', 1, type=int)
    hours = max(1, min(hours, 24))

    data = get_vm_metrics(rg, vm_name, hours)
    return jsonify(data)


@admin_bp.route('/performance')
@login_required
def performance():
    """
    VM Performance Metrics dashboard.
    Builds a Subscription → Resource Group → OS tree from get_all_vms()
    and renders it as an expandable left-panel alongside on-demand charts.
    """
    import re
    from collections import defaultdict

    def _safe_id(*parts):
        """Produce a valid HTML id from arbitrary strings."""
        combined = '-'.join(str(p) for p in parts)
        return re.sub(r'[^a-z0-9]+', '-', combined.lower()).strip('-')

    raw_vms = get_all_vms()

    # Group: sub_name → rg_name → os_type → [vm, ...]
    sub_map = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for vm in raw_vms:
        sub_map[vm['subscription_name']][vm['resource_group']][vm['os_type']].append(vm)

    tree = []
    for sub_name, rg_map in sub_map.items():
        sub_id = _safe_id(sub_name)
        rgs = []
        for rg_name, os_map in sorted(rg_map.items()):
            rg_id = _safe_id(sub_name, rg_name)
            os_groups = []
            for os_name in sorted(os_map.keys()):
                os_id = _safe_id(sub_name, rg_name, os_name)
                icon  = 'fab fa-windows' if os_name == 'Windows' else 'fab fa-linux'
                os_groups.append({
                    'name': os_name,
                    'id':   os_id,
                    'icon': icon,
                    'vms':  sorted(os_map[os_name], key=lambda v: v['name']),
                })
            rgs.append({
                'name':      rg_name,
                'id':        rg_id,
                'os_groups': os_groups,
            })
        tree.append({
            'name': sub_name,
            'id':   sub_id,
            'rgs':  rgs,
        })

    return render_template(
        'admin/performance.html',
        tree=tree,
        vm_count=len(raw_vms),
    )


# ════════════════════════════════════════════════════════════════
# Decommission Queue — Module 16
# ════════════════════════════════════════════════════════════════

from datetime import datetime
from app.decommission.decom_service import (
    run_prechecks,
    soft_delete_vm,
    hard_delete_vm,
    restore_vm,
    send_decom_notification,
)
from app.servicenow.snow_service import close_change_request


@admin_bp.route('/decommission')
@login_required
def decommission_queue():
    """Admin queue showing all decommission requests."""
    all_decoms = DecommissionRequest.query.order_by(
        DecommissionRequest.created_at.desc()
    ).all()

    # Count badges for sidebar and summary
    active_states   = ['pending', 'prechecks_running',
                       'prechecks_done', 'soft_deleted']
    active_count    = sum(1 for d in all_decoms if d.state in active_states)
    overdue_count   = sum(1 for d in all_decoms if d.is_overdue()
                          and d.state == 'soft_deleted')

    return render_template(
        'admin/decommission.html',
        decoms        = all_decoms,
        active_count  = active_count,
        overdue_count = overdue_count,
    )


@admin_bp.route('/decommission/<int:decom_id>/detail')
@login_required
def decommission_detail(decom_id):
    """Return full record as JSON for the detail modal."""
    decom = DecommissionRequest.query.get_or_404(decom_id)
    return jsonify(decom.to_dict())


@admin_bp.route('/decommission/<int:decom_id>/prechecks', methods=['POST'])
@login_required
def decommission_prechecks(decom_id):
    """Run pre-checks: metadata + DNS + snapshot."""
    decom = DecommissionRequest.query.get_or_404(decom_id)

    if decom.state not in ('pending', 'prechecks_failed'):
        return jsonify({
            'success': False,
            'message': f'Cannot run pre-checks in state: {decom.state}',
        }), 400

    decom.state = 'prechecks_running'
    db.session.commit()

    result = run_prechecks(decom)

    if result['success']:
        decom.state = 'prechecks_done'
        decom.error_message = None
        send_decom_notification(decom, 'prechecks_done')
    else:
        decom.state         = 'prechecks_failed'
        decom.error_message = result.get('error')
        send_decom_notification(decom, 'prechecks_failed')

    db.session.commit()
    return jsonify({
        'success': result['success'],
        'state':   decom.state,
        'message': result.get('notes') or result.get('error', ''),
    })


@admin_bp.route('/decommission/<int:decom_id>/soft-delete', methods=['POST'])
@login_required
def decommission_soft_delete(decom_id):
    """Stop VM + apply decom tags.  Starts 30-day advisory clock."""
    decom = DecommissionRequest.query.get_or_404(decom_id)

    if decom.state != 'prechecks_done':
        return jsonify({
            'success': False,
            'message': f'Pre-checks must be complete before soft-delete (state: {decom.state})',
        }), 400

    result = soft_delete_vm(decom)

    if result['success']:
        from datetime import timedelta
        now = datetime.utcnow()
        decom.state           = 'soft_deleted'
        decom.soft_deleted_at = now
        decom.hard_delete_due = now + timedelta(days=30)
        decom.error_message   = None
        send_decom_notification(decom, 'soft_deleted')
    else:
        decom.error_message = result.get('error')

    db.session.commit()
    return jsonify({
        'success': result['success'],
        'state':   decom.state,
        'message': result.get('error') or 'VM soft-deleted. 30-day holding period started.',
    })


@admin_bp.route('/decommission/<int:decom_id>/hard-delete', methods=['POST'])
@login_required
def decommission_hard_delete(decom_id):
    """
    IRREVERSIBLE — delete VM, disks, NICs, public IPs.
    Requires JSON body: {"confirm_vm_name": "<vm_name>"}
    """
    decom = DecommissionRequest.query.get_or_404(decom_id)

    if decom.state != 'soft_deleted':
        return jsonify({
            'success': False,
            'message': f'VM must be in soft_deleted state (current: {decom.state})',
        }), 400

    # Safety gate: caller must echo back the VM name
    data = request.get_json(silent=True) or {}
    if data.get('confirm_vm_name', '').strip() != decom.vm_name:
        return jsonify({
            'success': False,
            'message': 'VM name confirmation does not match. Deletion cancelled.',
        }), 400

    user  = session.get('user', {})
    admin = user.get('email', 'unknown-admin')

    result = hard_delete_vm(decom)

    if result['success']:
        decom.state        = 'hard_deleted'
        decom.completed_at = datetime.utcnow()
        decom.completed_by = admin
        decom.error_message = None
        # Auto-close SNOW CHG ticket
        if decom.snow_ticket:
            snow_result = close_change_request(
                decom.snow_ticket,
                f'VM {decom.vm_name} permanently decommissioned via IaaS Portal '
                f'by {admin}. Resources deleted: '
                + ', '.join(result.get('deleted', [])),
                vm_name=decom.vm_name,
            )
            if not snow_result['success']:
                print(f'[WARN]  SNOW close failed for #{decom_id}: '
                      f'{snow_result.get("error")}')
        send_decom_notification(decom, 'hard_deleted')
    else:
        decom.error_message = result.get('error')

    db.session.commit()

    deleted_list = result.get('deleted', [])
    errors_list  = result.get('errors',  [])
    return jsonify({
        'success':  result['success'],
        'state':    decom.state,
        'deleted':  deleted_list,
        'errors':   errors_list,
        'message':  (
            f'Deleted: {", ".join(deleted_list)}.'
            + (f' Warnings: {", ".join(errors_list)}' if errors_list else '')
        ) if result['success'] else result.get('error', 'Hard delete failed'),
    })


@admin_bp.route('/decommission/<int:decom_id>/restore', methods=['POST'])
@login_required
def decommission_restore(decom_id):
    """Start VM + remove decom tags.  SNOW ticket stays open."""
    decom = DecommissionRequest.query.get_or_404(decom_id)

    if decom.state != 'soft_deleted':
        return jsonify({
            'success': False,
            'message': f'VM must be in soft_deleted state to restore (current: {decom.state})',
        }), 400

    user  = session.get('user', {})
    admin = user.get('email', 'unknown-admin')

    result = restore_vm(decom)

    if result['success']:
        decom.state         = 'restored'
        decom.completed_at  = datetime.utcnow()
        decom.completed_by  = admin
        decom.error_message = None
        send_decom_notification(decom, 'restored')
    else:
        decom.error_message = result.get('error')

    db.session.commit()
    return jsonify({
        'success': result['success'],
        'state':   decom.state,
        'message': result.get('error') or 'VM restored. SNOW ticket remains open — close manually if required.',
    })


@admin_bp.route('/decommission/<int:decom_id>/notify', methods=['POST'])
@login_required
def decommission_resend_notify(decom_id):
    """Manually re-send the current-state notification email."""
    decom = DecommissionRequest.query.get_or_404(decom_id)

    state_to_phase = {
        'pending':           'queued',
        'prechecks_done':    'prechecks_done',
        'prechecks_failed':  'prechecks_failed',
        'soft_deleted':      'soft_deleted',
        'hard_deleted':      'hard_deleted',
        'restored':          'restored',
    }
    phase = state_to_phase.get(decom.state, decom.state)
    send_decom_notification(decom, phase)
    return jsonify({'success': True, 'message': 'Notification sent.'})
