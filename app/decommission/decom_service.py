# ═══════════════════════════════════════════════════════════════
# Decommission Service — Module 16
#
# Azure operations for the full decommission lifecycle:
#   run_prechecks()   — snapshot + metadata + DNS capture
#   soft_delete_vm()  — stop VM + apply decom tags
#   hard_delete_vm()  — delete VM, disks, NICs, public IPs
#   restore_vm()      — start VM + remove decom tags
#   send_decom_notification() — phase-change email to CAB approver
# ═══════════════════════════════════════════════════════════════

import json
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient

from config import Config
from app.vm.dns_service import get_vm_dns_config


# ── Azure clients ─────────────────────────────────────────────

def _get_clients():
    cred = ClientSecretCredential(
        tenant_id     = Config.TENANT_ID,
        client_id     = Config.CLIENT_ID,
        client_secret = Config.CLIENT_SECRET,
    )
    return (
        ComputeManagementClient(cred, Config.SUBSCRIPTION_ID),
        NetworkManagementClient(cred, Config.SUBSCRIPTION_ID),
    )


# ── Pre-checks ────────────────────────────────────────────────

def run_prechecks(decom) -> dict:
    """
    Phase 1: capture everything needed before deletion.

    Steps:
      1. Fetch full VM object → store as metadata_json
      2. Fetch DNS config → store as dns_records
      3. Create OS disk snapshot → store snapshot_name + snapshot_id

    Returns {'success': bool, 'notes': str, 'error': str}
    All three steps are attempted; failure of any returns success=False.
    Snapshot creation waits up to 180 seconds.
    """
    compute_client, network_client = _get_clients()
    notes = []

    # ── Step 1: VM metadata ───────────────────────────────────
    try:
        vm = compute_client.virtual_machines.get(
            decom.resource_group, decom.vm_name,
            expand='instanceView'
        )
        data_disks = []
        for d in vm.storage_profile.data_disks:
            data_disks.append({
                'name': d.name,
                'lun':  d.lun,
                'id':   d.managed_disk.id if d.managed_disk else None,
            })

        nics = []
        for n in vm.network_profile.network_interfaces:
            nics.append({'id': n.id})

        os_disk_id = None
        if (vm.storage_profile.os_disk.managed_disk):
            os_disk_id = vm.storage_profile.os_disk.managed_disk.id

        metadata = {
            'vm_id':            vm.id,
            'location':         vm.location,
            'vm_size':          vm.hardware_profile.vm_size,
            'os_type':          str(vm.storage_profile.os_disk.os_type),
            'os_disk_name':     vm.storage_profile.os_disk.name,
            'os_disk_id':       os_disk_id,
            'data_disks':       data_disks,
            'nics':             nics,
            'tags':             vm.tags or {},
            'availability_set': vm.availability_set.id if vm.availability_set else None,
            'captured_at':      datetime.utcnow().isoformat(),
        }
        decom.metadata_json = json.dumps(metadata, indent=2)

        if data_disks:
            notes.append(
                f'VM has {len(data_disks)} data disk(s) — '
                'all will be deleted on hard-delete.'
            )

    except Exception as e:
        return {
            'success': False,
            'error':   f'Metadata capture failed: {e}',
        }

    # ── Step 2: DNS config ────────────────────────────────────
    try:
        dns = get_vm_dns_config(decom.resource_group, decom.vm_name)
        lines = [
            f"Hostname   : {dns.get('hostname', 'N/A')}",
            f"FQDN       : {dns.get('fqdn', 'N/A')}",
            f"DNS Servers: {', '.join(dns.get('dns_servers', [])) or 'Azure default'}",
            f"Search Sfx : {', '.join(dns.get('search_suffixes', [])) or 'none'}",
        ]
        decom.dns_records = '\n'.join(lines)
    except Exception as e:
        decom.dns_records = f'DNS capture failed: {e}'
        notes.append(f'DNS capture warning: {e}')

    # ── Step 3: OS disk snapshot ──────────────────────────────
    try:
        meta = json.loads(decom.metadata_json)
        os_disk_id = meta.get('os_disk_id')
        location   = meta.get('location', 'eastus')

        if not os_disk_id:
            notes.append('VM has no managed OS disk — snapshot skipped.')
        else:
            snap_name = (
                f'decom-{decom.vm_name}-'
                f'{datetime.utcnow().strftime("%Y%m%d%H%M%S")}'
            )
            snap_params = {
                'location': location,
                'creation_data': {
                    'create_option': 'Copy',
                    'source_uri':    os_disk_id,
                },
            }
            poller   = compute_client.snapshots.begin_create_or_update(
                decom.resource_group, snap_name, snap_params
            )
            snapshot = poller.result(timeout=180)   # wait up to 3 min
            decom.snapshot_name = snapshot.name
            decom.snapshot_id   = snapshot.id
            notes.append(f'Snapshot created: {snapshot.name}')

    except Exception as e:
        return {
            'success': False,
            'error':   f'Snapshot creation failed: {e}',
        }

    decom.precheck_notes = '\n'.join(notes) if notes else 'All checks passed.'
    return {'success': True, 'notes': decom.precheck_notes, 'error': None}


# ── Soft Delete ───────────────────────────────────────────────

def soft_delete_vm(decom) -> dict:
    """
    Phase 2: stop VM and mark it with decom tags.

    Does NOT delete any resources.  Starts the 30-day advisory clock.
    Tags applied:
      DecomStatus  : SoftDeleted
      DecomDate    : YYYY-MM-DD
      DecomTicket  : CHG0012345
      DecomDueDate : YYYY-MM-DD  (soft_deleted_at + 30 days)
    """
    compute_client, _ = _get_clients()

    # Stop / deallocate
    try:
        poller = compute_client.virtual_machines.begin_deallocate(
            decom.resource_group, decom.vm_name
        )
        poller.result(timeout=300)
    except Exception as e:
        return {'success': False, 'error': f'VM stop failed: {e}'}

    # Apply decom tags (merge with existing tags)
    try:
        vm       = compute_client.virtual_machines.get(
            decom.resource_group, decom.vm_name
        )
        now      = datetime.utcnow()
        due_date = now + timedelta(days=30)
        tags     = dict(vm.tags or {})
        tags.update({
            'DecomStatus':  'SoftDeleted',
            'DecomDate':    now.strftime('%Y-%m-%d'),
            'DecomTicket':  decom.snow_ticket or 'N/A',
            'DecomDueDate': due_date.strftime('%Y-%m-%d'),
        })
        compute_client.virtual_machines.begin_update(
            decom.resource_group,
            decom.vm_name,
            {'tags': tags}
        ).result(timeout=60)

    except Exception as e:
        # VM is stopped but tags failed — partial success
        return {
            'success': False,
            'error':   f'VM stopped but tagging failed: {e}',
        }

    return {'success': True, 'error': None}


# ── Hard Delete ───────────────────────────────────────────────

def hard_delete_vm(decom) -> dict:
    """
    Phase 3 (IRREVERSIBLE): delete VM, OS disk, data disks, NICs, public IPs.

    Uses metadata_json captured during pre-checks to identify
    all resources.  Each deletion is attempted independently so
    a failure on one resource does not block the others.

    Returns {'success': bool, 'deleted': [...], 'errors': [...]}
    """
    compute_client, network_client = _get_clients()
    deleted = []
    errors  = []

    if not decom.metadata_json:
        return {
            'success': False,
            'error':   'No metadata — run pre-checks first.',
        }

    meta = json.loads(decom.metadata_json)
    rg   = decom.resource_group

    # ── 1. Delete VM ──────────────────────────────────────────
    try:
        compute_client.virtual_machines.begin_delete(
            rg, decom.vm_name
        ).result(timeout=300)
        deleted.append(f'VM: {decom.vm_name}')
    except Exception as e:
        errors.append(f'VM delete failed: {e}')
        # Cannot proceed without VM deletion — stop here
        return {
            'success':  False,
            'deleted':  deleted,
            'errors':   errors,
            'error':    f'VM delete failed: {e}',
        }

    # ── 2. Delete OS disk ─────────────────────────────────────
    os_disk_name = meta.get('os_disk_name')
    if os_disk_name:
        try:
            compute_client.disks.begin_delete(
                rg, os_disk_name
            ).result(timeout=180)
            deleted.append(f'OS disk: {os_disk_name}')
        except Exception as e:
            errors.append(f'OS disk delete failed: {e}')

    # ── 3. Delete data disks ──────────────────────────────────
    for disk in meta.get('data_disks', []):
        disk_name = disk.get('name')
        if not disk_name:
            continue
        try:
            compute_client.disks.begin_delete(
                rg, disk_name
            ).result(timeout=180)
            deleted.append(f'Data disk: {disk_name}')
        except Exception as e:
            errors.append(f'Data disk {disk_name} delete failed: {e}')

    # ── 4. Delete NICs + public IPs ───────────────────────────
    for nic_ref in meta.get('nics', []):
        nic_id   = nic_ref.get('id', '')
        nic_rg   = nic_id.split('/')[4] if nic_id.count('/') >= 4 else rg
        nic_name = nic_id.split('/')[-1]
        if not nic_name:
            continue

        # Collect public IPs before deleting the NIC
        pip_names = []
        try:
            nic = network_client.network_interfaces.get(nic_rg, nic_name)
            for ip_cfg in nic.ip_configurations:
                if ip_cfg.public_ip_address:
                    pip_id   = ip_cfg.public_ip_address.id
                    pip_rg   = pip_id.split('/')[4]
                    pip_name = pip_id.split('/')[-1]
                    pip_names.append((pip_rg, pip_name))
        except Exception:
            pass   # NIC may already be gone

        try:
            network_client.network_interfaces.begin_delete(
                nic_rg, nic_name
            ).result(timeout=180)
            deleted.append(f'NIC: {nic_name}')
        except Exception as e:
            errors.append(f'NIC {nic_name} delete failed: {e}')

        for pip_rg, pip_name in pip_names:
            try:
                network_client.public_ip_addresses.begin_delete(
                    pip_rg, pip_name
                ).result(timeout=60)
                deleted.append(f'Public IP: {pip_name}')
            except Exception as e:
                errors.append(f'Public IP {pip_name} delete failed: {e}')

    return {
        'success': len(errors) == 0,
        'deleted': deleted,
        'errors':  errors,
        'error':   '; '.join(errors) if errors else None,
    }


# ── Restore ───────────────────────────────────────────────────

def restore_vm(decom) -> dict:
    """
    Reversal path: start VM + remove all decom tags.

    SNOW ticket is NOT closed — admin must handle that manually.
    """
    compute_client, _ = _get_clients()

    # Start VM
    try:
        compute_client.virtual_machines.begin_start(
            decom.resource_group, decom.vm_name
        ).result(timeout=300)
    except Exception as e:
        return {'success': False, 'error': f'VM start failed: {e}'}

    # Remove decom tags
    decom_tag_keys = {'DecomStatus', 'DecomDate', 'DecomTicket', 'DecomDueDate'}
    try:
        vm   = compute_client.virtual_machines.get(
            decom.resource_group, decom.vm_name
        )
        tags = {k: v for k, v in (vm.tags or {}).items()
                if k not in decom_tag_keys}
        compute_client.virtual_machines.begin_update(
            decom.resource_group,
            decom.vm_name,
            {'tags': tags}
        ).result(timeout=60)
    except Exception as e:
        return {
            'success': False,
            'error':   f'VM started but tag removal failed: {e}',
        }

    return {'success': True, 'error': None}


# ── Phase email notification ──────────────────────────────────

# Human-readable phase labels for email subjects
_PHASE_SUBJECTS = {
    'queued':           'Decommission request received',
    'prechecks_done':   'Pre-checks complete — ready for soft-delete',
    'prechecks_failed': 'Pre-checks failed — action required',
    'soft_deleted':     'VM soft-deleted — 30-day holding period started',
    'hard_deleted':     'VM permanently deleted — decommission complete',
    'restored':         'VM restored from decommission hold',
}


def send_decom_notification(decom, phase: str) -> None:
    """
    Sends a phase-change email to the CAB approver (if email set)
    and the SNOW caller (if it looks like an email address).

    Silently skips if MAIL_USERNAME is not configured.
    """
    if not Config.MAIL_USERNAME:
        print(f'Email not configured — skipping decom notification ({phase})')
        return

    # Build recipient list (deduplicated)
    recipients = set()
    if decom.cab_approver_email:
        recipients.add(decom.cab_approver_email)
    caller = decom.snow_caller or ''
    if '@' in caller:
        recipients.add(caller)

    if not recipients:
        print(f'No email recipients for decom #{decom.id} ({phase})')
        return

    subject = (
        f'[IaaS Portal] {_PHASE_SUBJECTS.get(phase, phase)} '
        f'— {decom.vm_name}'
    )
    body = _build_email_body(decom, phase)

    for recipient in recipients:
        _send_email(recipient, subject, body)


def _build_email_body(decom, phase: str) -> str:
    phase_label = _PHASE_SUBJECTS.get(phase, phase)
    due_line    = ''
    if decom.hard_delete_due:
        due_line = (
            f'<tr style="background:#fff3cd">'
            f'<td><b>Hard-Delete Due</b></td>'
            f'<td>{decom.hard_delete_due.strftime("%Y-%m-%d")} '
            f'(30 days — advisory)</td></tr>'
        )

    cab_rows = ''
    if decom.cab_approval_number or decom.cab_approver_name:
        cab_rows = f"""
        <tr><td colspan="2"
               style="background:#e8f4f8; font-weight:bold;
                      padding:6px 8px">
            CAB Approval Reference</td></tr>
        <tr style="background:#f0f0f0">
            <td><b>Approval Number</b></td>
            <td>{decom.cab_approval_number or '—'}</td>
        </tr>
        <tr>
            <td><b>Approver</b></td>
            <td>{decom.cab_approver_name or '—'}
                ({decom.cab_approver_dept or '—'})</td>
        </tr>
        """

    return f"""
    <html><body style="font-family:sans-serif">
    <h2 style="color:#0078d4">
        IaaS Self-Service Portal — VM Decommission Update
    </h2>
    <p><b>Status: {phase_label.upper()}</b></p>
    <table border="1" cellpadding="8"
           style="border-collapse:collapse; width:100%; max-width:600px">
        <tr style="background:#f0f0f0">
            <td><b>VM Name</b></td>
            <td>{decom.vm_name}</td>
        </tr>
        <tr>
            <td><b>Resource Group</b></td>
            <td>{decom.resource_group}</td>
        </tr>
        <tr style="background:#f0f0f0">
            <td><b>SNOW Ticket</b></td>
            <td>{decom.snow_ticket or '—'}</td>
        </tr>
        <tr>
            <td><b>Portal Reference</b></td>
            <td>Decommission #{decom.id}</td>
        </tr>
        <tr style="background:#f0f0f0">
            <td><b>Current State</b></td>
            <td>{decom.state}</td>
        </tr>
        {due_line}
        {cab_rows}
    </table>
    <br>
    <p style="color:grey; font-size:12px">
        IaaS Self-Service Tool — Azure Innovation Team<br>
        This is an automated notification.
        Log in to the admin portal to view full details.
    </p>
    </body></html>
    """


def _send_email(to_addr: str, subject: str, html_body: str) -> None:
    for attempt in range(3):
        try:
            msg            = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = Config.MAIL_DEFAULT_SENDER
            msg['To']      = to_addr
            msg.attach(MIMEText(html_body, 'html'))

            with smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)
                server.sendmail(
                    Config.MAIL_DEFAULT_SENDER,
                    to_addr,
                    msg.as_string()
                )
            print(f'✅ Decom email sent to {to_addr}')
            return

        except smtplib.SMTPAuthenticationError:
            print(f'❌ SMTP auth failed for {to_addr}')
            return

        except Exception as e:
            wait = (attempt + 1) * 5
            print(f'⚠️  Email attempt {attempt + 1}/3 failed: {e}')
            if attempt < 2:
                time.sleep(wait)
