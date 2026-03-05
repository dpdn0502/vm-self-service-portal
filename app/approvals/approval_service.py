import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from config import Config
from models import db, ApprovalRequest
from app.servicenow.snow_service import create_incident


# Risk classification — drives approval urgency and SNOW priority
RISK_LEVELS = {
    'resize':          'medium',
    'disk_attach':     'medium',
    'disk_detach':     'medium',
    'os_disk_swap':    'high',
    'backup':          'high',
    'snapshot_create': 'medium',
    'snapshot_delete': 'high',
    'tag_update':      'medium',   # tag add/change — governance impact
    'tag_delete':      'high',     # tag removal — could break automation
    'tag_bulk_update':  'medium',   # bulk tag set — governance impact
    'timezone_change':  'medium',   # OS timezone change
    'dns_hostname_change': 'medium', # VM hostname change via Run Command
    'dns_server_update':   'medium', # NIC custom DNS server list
    'dns_suffix_change':   'medium', # OS DNS search suffixes
    'patch_assess':        'low',    # trigger guest patch assessment
    'patch_install':       'high',   # install patches on a running VM
    'patch_mode_set':      'medium', # configure AUM patch mode
    'patch_reboot':        'medium', # reboot VM for pending patches
}

# Human-readable action names shown in emails and portal
ACTION_LABELS = {
    'resize':          'Resize VM (SKU Change)',
    'disk_attach':     'Attach Data Disk',
    'disk_detach':     'Detach Data Disk',
    'os_disk_swap':    'OS Disk Swap',
    'backup':          'Backup Management',
    'snapshot_create': 'Create Disk Snapshot',
    'snapshot_delete': 'Delete Disk Snapshot',
    'tag_update':      'Update VM Tag',
    'tag_delete':      'Delete VM Tag',
    'tag_bulk_update': 'Bulk Update VM Tags',
    'timezone_change':     'Change VM Timezone',
    'dns_hostname_change': 'Change VM Hostname',
    'dns_server_update':   'Update NIC DNS Servers',
    'dns_suffix_change':   'Update DNS Search Suffixes',
    'patch_assess':        'Trigger Patch Assessment',
    'patch_install':       'Install VM Patches',
    'patch_mode_set':      'Set VM Patch Mode',
    'patch_reboot':        'Reboot VM for Pending Patches',
}


def create_approval_request(user, vm_name, resource_group,
                             action, action_details):
    """
    Creates approval request in database
    Notifies both approvers via email and ServiceNow
    """
    risk_level = RISK_LEVELS.get(action, 'medium')

    # ── Create DB record ──────────────────────────────────────
    approval = ApprovalRequest(
        requester_email  = user.get('preferred_username'),
        requester_name   = user.get('name'),
        vm_name          = vm_name,
        resource_group   = resource_group,
        action           = action,
        action_details   = action_details,
        risk_level       = risk_level,
        status           = 'pending',
        approver1_email  = Config.APPROVER1_EMAIL,
        approver2_email  = Config.APPROVER2_EMAIL,
        approver1_azure  = Config.APPROVER1_AZURE,
        approver2_azure  = Config.APPROVER2_AZURE,
        approver1_status = 'pending',
        approver2_status = 'pending'
    )
    db.session.add(approval)
    db.session.commit()

    # ── Send approval emails ──────────────────────────────────
    try:
        send_approval_email(approval)
    except Exception as e:
        print(f"⚠️ Email failed but approval created: {e}")

    # ── Create ServiceNow ticket ──────────────────────────────
    try:
        snow_result = create_incident(
            vm_name        = vm_name,
            action         = action,
            user_name      = user.get('name'),
            user_email     = user.get('preferred_username'),
            resource_group = resource_group,
            status         = 'pending',
            message        = (
                f"Approval required for "
                f"{ACTION_LABELS.get(action, action)}\n"
                f"Details: {action_details}\n"
                f"Risk Level: {risk_level.upper()}\n"
                f"Approval URL: "
                f"http://localhost:5000/approvals/{approval.id}"
            )
        )

        # ── Save ticket details including sys_id ──────────────
        if snow_result.get('success'):
            approval.snow_ticket      = snow_result.get(
                                            'incident_number'
                                        )
            approval.snow_ticket_url  = snow_result.get(
                                            'incident_url'
                                        )
            approval.snow_sys_id      = snow_result.get(
                                            'incident_sys_id'
                                        )
            approval.snow_ticket_type = snow_result.get(
                                            'ticket_type',
                                            'change_request'
                                        )
            db.session.commit()
            print(f"✅ SNOW ticket saved: "
                  f"{approval.snow_ticket}")

    except Exception as e:
        print(f"⚠️ ServiceNow failed but "
              f"approval created: {e}")

    return approval


def send_approval_email(approval):
    """Sends email notification to both approvers"""
    if not Config.MAIL_USERNAME:
        print("Email not configured — skipping")
        return

    subject = (
        f"[APPROVAL REQUIRED] "
        f"{ACTION_LABELS.get(approval.action, approval.action)}"
        f" — {approval.vm_name}"
    )

    body = f"""
    <html><body>
    <h2 style="color:#0078d4">
        ⚠️ VM Action Approval Required
    </h2>
    <table border="1" cellpadding="8"
           style="border-collapse:collapse; width:100%">
        <tr style="background:#f0f0f0">
            <td><b>Requested By</b></td>
            <td>{approval.requester_name}
                ({approval.requester_email})</td>
        </tr>
        <tr>
            <td><b>VM Name</b></td>
            <td>{approval.vm_name}</td>
        </tr>
        <tr style="background:#f0f0f0">
            <td><b>Resource Group</b></td>
            <td>{approval.resource_group}</td>
        </tr>
        <tr>
            <td><b>Action</b></td>
            <td>{ACTION_LABELS.get(approval.action,
                                   approval.action)}</td>
        </tr>
        <tr style="background:#f0f0f0">
            <td><b>Details</b></td>
            <td>{approval.action_details}</td>
        </tr>
        <tr>
            <td><b>Risk Level</b></td>
            <td>{approval.risk_level.upper()}</td>
        </tr>
        <tr style="background:#f0f0f0">
            <td><b>Requested At</b></td>
            <td>{approval.created_at.strftime(
                    '%Y-%m-%d %H:%M:%S')} UTC</td>
        </tr>
        <tr>
            <td><b>ServiceNow Ticket</b></td>
            <td>{approval.snow_ticket or 'Pending'}</td>
        </tr>
    </table>
    <br>
    <a href="http://localhost:5000/auth/login?next=http://localhost:5000/approvals/{approval.id}"
       style="background:#0078d4; color:white;
              padding:12px 24px; text-decoration:none;
              border-radius:4px; font-weight:bold">
        ✅ Review &amp; Approve / Reject
    </a>
    <p style="color:grey; font-size:12px">
        Requires <b>2 approvals</b> before executing.<br>
        IaaS Self-Service Tool — Auto generated
    </p>
    </body></html>
    """

    for approver_email in [Config.APPROVER1_EMAIL,
                            Config.APPROVER2_EMAIL]:
        for attempt in range(3):
            try:
                msg            = MIMEMultipart('alternative')
                msg['Subject'] = subject
                msg['From']    = Config.MAIL_DEFAULT_SENDER
                msg['To']      = approver_email
                msg.attach(MIMEText(body, 'html'))

                with smtplib.SMTP(
                    Config.MAIL_SERVER,
                    Config.MAIL_PORT
                ) as server:
                    server.ehlo()
                    server.starttls()
                    server.login(
                        Config.MAIL_USERNAME,
                        Config.MAIL_PASSWORD
                    )
                    server.sendmail(
                        Config.MAIL_DEFAULT_SENDER,
                        approver_email,
                        msg.as_string()
                    )
                print(f"✅ Email sent to {approver_email}")
                break

            except smtplib.SMTPAuthenticationError:
                print(f"❌ Auth failed for {approver_email}")
                break

            except Exception as e:
                wait = (attempt + 1) * 5
                print(f"⚠️ Attempt {attempt+1}/3: {e}")
                if attempt < 2:
                    time.sleep(wait)


def process_approval_decision(approval_id, approver_email,
                               decision, comment,
                               is_admin=False):
    """
    approver_email is Azure login email from session
    Checks against approver1_azure and approver2_azure
    """
    approval = ApprovalRequest.query.get(approval_id)

    if not approval:
        return {
            'success': False,
            'error':   'Approval request not found'
        }

    if approval.status != 'pending':
        return {
            'success': False,
            'error':   'This request is no longer pending'
        }

    now           = datetime.utcnow()
    decision_made = False

    print(f"─── APPROVAL DECISION ──────────────────────")
    print(f"Approver:    {approver_email}")
    print(f"Decision:    {decision}")
    print(f"Approver1:   {approval.approver1_azure}")
    print(f"Approver2:   {approval.approver2_azure}")
    print(f"────────────────────────────────────────────")

    # Check Approver 1
    if (approver_email == approval.approver1_azure and
            approval.approver1_status == 'pending'):

        approval.approver1_status  = decision
        approval.approver1_comment = comment
        approval.approver1_at      = now
        approval.approver1_name    = approver_email
        decision_made              = True
        print(f"✅ Approver 1 recorded: {decision}")

        # Same person is both approvers
        if (approver_email == approval.approver2_azure and
                approval.approver2_status == 'pending'):
            approval.approver2_status  = decision
            approval.approver2_comment = comment
            approval.approver2_at      = now
            approval.approver2_name    = approver_email
            print(f"✅ Approver 2 also recorded: {decision}")

    # Check Approver 2 only
    elif (approver_email == approval.approver2_azure and
            approval.approver2_status == 'pending'):

        approval.approver2_status  = decision
        approval.approver2_comment = comment
        approval.approver2_at      = now
        approval.approver2_name    = approver_email
        decision_made              = True
        print(f"✅ Approver 2 recorded: {decision}")

    if not decision_made and is_admin:
        # Admin override — fill whichever approver slot is still pending
        now = datetime.utcnow()
        if approval.approver1_status == 'pending':
            approval.approver1_status  = decision
            approval.approver1_comment = comment
            approval.approver1_at      = now
            approval.approver1_name    = approver_email
            decision_made              = True
            print(f"✅ Admin override — Approver 1 recorded: {decision}")
            # If both slots same person or approver2 already decided
            if approval.approver2_status == 'pending':
                approval.approver2_status  = decision
                approval.approver2_comment = comment
                approval.approver2_at      = now
                approval.approver2_name    = approver_email
                print(f"✅ Admin override — Approver 2 also recorded: {decision}")
        elif approval.approver2_status == 'pending':
            approval.approver2_status  = decision
            approval.approver2_comment = comment
            approval.approver2_at      = now
            approval.approver2_name    = approver_email
            decision_made              = True
            print(f"✅ Admin override — Approver 2 recorded: {decision}")

    if not decision_made:
        return {
            'success': False,
            'error':   'You are not an approver '
                       'or already decided'
        }

    db.session.commit()

    # Check if rejected
    if approval.either_rejected():
        approval.status = 'rejected'
        db.session.commit()
        notify_requester(approval, 'rejected')
        return {
            'success': True,
            'status':  'rejected',
            'message': 'Request rejected — requester notified'
        }

    # Check if both approved
    if approval.both_approved():
        approval.status = 'approved'
        db.session.commit()
        print("🚀 Both approved — executing now...")
        result = execute_approved_action(approval)
        return result

    # Waiting for second approver
    return {
        'success': True,
        'status':  'waiting',
        'message': 'Decision recorded — waiting for '
                   'second approver'
    }


def execute_approved_action(approval):
    """Executes Azure action after both approvals"""
    from app.vm.azure_service import resize_vm
    from app.vm.disk_service import attach_disk, detach_disk
    from app.vm.snapshot_service import (
        create_snapshot,
        delete_snapshot
    )
    # Import tag service for tag_update / tag_delete / tag_bulk_update
    from app.vm.tag_service import (
        update_vm_tag, delete_vm_tag, bulk_update_vm_tags
    )

    print(f"─── EXECUTING ACTION ───────────────────────")
    print(f"Action:  {approval.action}")
    print(f"Details: {approval.action_details}")
    print(f"VM:      {approval.vm_name}")
    print(f"RG:      {approval.resource_group}")
    print(f"────────────────────────────────────────────")

    try:
        # Parse action details into key value pairs
        details = {}
        for part in approval.action_details.split('|'):
            if ':' in part:
                key, val = part.split(':', 1)
                details[key.strip()] = val.strip()

        print(f"Parsed details: {details}")

        # ── Resize ────────────────────────────────────────────
        if approval.action == 'resize':
            parts    = approval.action_details.split('|')
            new_size = parts[1].strip()
            message  = resize_vm(
                approval.resource_group,
                approval.vm_name,
                new_size
            )

        # ── Disk Attach ───────────────────────────────────────
        elif approval.action == 'disk_attach':
            disk_name = details.get('Disk', '')
            disk_id   = details.get('DiskID', '')
            message   = attach_disk(
                approval.resource_group,
                approval.vm_name,
                disk_name,
                disk_id
            )

        # ── Disk Detach ───────────────────────────────────────
        elif approval.action == 'disk_detach':
            disk_name = details.get('Disk', '')
            message   = detach_disk(
                approval.resource_group,
                approval.vm_name,
                disk_name
            )

        # ── Snapshot Create ───────────────────────────────────
        elif approval.action == 'snapshot_create':
            disk_name = details.get('Disk', '')
            result    = create_snapshot(
                approval.resource_group,
                approval.vm_name,
                disk_name
            )
            message   = (
                f"Snapshot '{result['name']}' created "
                f"successfully ({result['size_gb']}GB)"
            )

        # ── Snapshot Delete ───────────────────────────────────
        elif approval.action == 'snapshot_delete':
            snapshot_name = details.get('Snapshot', '')
            message       = delete_snapshot(
                approval.resource_group,
                snapshot_name
            )

        # ── Tag Update — set/add a single tag key ─────────────
        elif approval.action == 'tag_update':
            tag_key   = details.get('Key', '')
            tag_value = details.get('Value', '')
            message   = update_vm_tag(
                approval.resource_group,
                approval.vm_name,
                tag_key,
                tag_value
            )

        # ── Tag Bulk Update — set multiple tags at once ───────
        elif approval.action == 'tag_bulk_update':
            import json
            # action_details stored as JSON string: {"Key": "Value", ...}
            tags_dict = json.loads(approval.action_details)
            message   = bulk_update_vm_tags(
                approval.resource_group,
                approval.vm_name,
                tags_dict
            )

        # ── Tag Delete — remove a single tag key ──────────────
        elif approval.action == 'tag_delete':
            tag_key = details.get('Key', '')
            message = delete_vm_tag(
                approval.resource_group,
                approval.vm_name,
                tag_key
            )

        # ── Timezone Change — sets OS timezone via Run Command ─
        elif approval.action == 'timezone_change':
            from app.vm.timezone_service import set_vm_timezone
            os_type     = details.get('OS', 'Linux')
            timezone_id = details.get('Timezone', 'UTC')
            message     = set_vm_timezone(
                approval.resource_group,
                approval.vm_name,
                os_type,
                timezone_id
            )

        # ── DNS Hostname Change — Run Command ──────────────────
        elif approval.action == 'dns_hostname_change':
            from app.vm.dns_service import change_vm_hostname
            os_type      = details.get('OS', 'Linux')
            new_hostname = details.get('Hostname', '')
            message      = change_vm_hostname(
                approval.resource_group,
                approval.vm_name,
                os_type,
                new_hostname
            )

        # ── DNS Server Update — NIC API ────────────────────────
        elif approval.action == 'dns_server_update':
            from app.vm.dns_service import update_nic_dns_servers
            nic_name    = details.get('NIC', '')
            nic_rg      = details.get('NIC_RG',
                              approval.resource_group)
            servers_raw = details.get('Servers', '')
            # Empty string means reset to Azure default
            dns_servers = [
                s.strip()
                for s in servers_raw.split(',')
                if s.strip()
            ]
            message = update_nic_dns_servers(
                nic_rg, nic_name, dns_servers
            )

        # ── DNS Search Suffix — Run Command ───────────────────
        elif approval.action == 'dns_suffix_change':
            from app.vm.dns_service import update_dns_search_suffix
            os_type      = details.get('OS', 'Linux')
            suffixes_raw = details.get('Suffixes', '')
            suffixes     = [
                s.strip()
                for s in suffixes_raw.split(',')
                if s.strip()
            ]
            message = update_dns_search_suffix(
                approval.resource_group,
                approval.vm_name,
                os_type,
                suffixes
            )

        # ── Patch Assessment — begin_assess_patches ────────────
        elif approval.action == 'patch_assess':
            from app.vm.patch_service import trigger_patch_assessment
            message = trigger_patch_assessment(
                approval.resource_group,
                approval.vm_name
            )

        # ── Patch Install — begin_install_patches ──────────────
        elif approval.action == 'patch_install':
            from app.vm.patch_service import install_patches
            os_type         = details.get('OS', 'Linux')
            class_raw       = details.get('Classifications', '')
            reboot_setting  = details.get(
                'Reboot', 'IfRequired'
            )
            classifications = [
                c.strip()
                for c in class_raw.split(',')
                if c.strip()
            ]
            message = install_patches(
                approval.resource_group,
                approval.vm_name,
                os_type,
                classifications,
                reboot_setting
            )

        # ── Patch Mode Set — configure AUM patch mode ──────────
        elif approval.action == 'patch_mode_set':
            from app.vm.patch_service import set_patch_mode
            os_type    = details.get('OS', 'Linux')
            patch_mode = details.get('Mode', 'AutomaticByPlatform')
            message    = set_patch_mode(
                approval.resource_group,
                approval.vm_name,
                os_type,
                patch_mode
            )

        # ── Patch Reboot — restart VM for pending patches ───────
        elif approval.action == 'patch_reboot':
            from app.vm.azure_service import restart_vm
            message = restart_vm(
                approval.resource_group,
                approval.vm_name
            )
            message = f"VM '{approval.vm_name}' restarted for patch application. {message}"

        else:
            message = f"Action {approval.action} executed"

        approval.status = 'executed'
        db.session.commit()

        # ── Auto-close ServiceNow ticket ──────────────────────
        try:
            from app.servicenow.snow_service import (
                update_ticket
            )

            if approval.snow_ticket and \
               approval.snow_sys_id:
                update_ticket(
                    sys_id      = approval.snow_sys_id,
                    ticket_type = (
                        approval.snow_ticket_type
                        or 'change_request'
                    ),
                    status      = 'success',
                    message     = message,
                    user_name   = approval.requester_name
                )
                print(f"✅ Ticket auto-closed: "
                      f"{approval.snow_ticket}")

        except Exception as e:
            print(f"⚠️ Auto-close failed: {e}")

        notify_requester(approval, 'executed', message)

        return {
            'success': True,
            'status':  'executed',
            'message': message
        }

    except Exception as e:
        import traceback
        print(f"❌ Execute failed: {e}")
        print(traceback.format_exc())

        approval.status = 'failed'
        db.session.commit()

        return {
            'success': False,
            'status':  'failed',
            'error':   str(e)
        }


def notify_requester(approval, status, message=''):
    """Emails the requester with approval outcome"""
    if not Config.MAIL_USERNAME:
        return

    status_text = {
        'rejected': '❌ Your request was rejected',
        'executed': '✅ Your request was approved '
                    'and executed'
    }.get(status, status)

    subject = (
        f"[IaaS Portal] {status_text} — "
        f"{approval.vm_name}"
    )

    snow_row = ''
    if approval.snow_ticket:
        snow_row = f"""
        <tr style="background:#f0f0f0">
            <td><b>ServiceNow Ticket</b></td>
            <td>{approval.snow_ticket}</td>
        </tr>"""

    body = f"""
    <html><body>
    <h2>{status_text}</h2>
    <table border="1" cellpadding="8"
           style="border-collapse:collapse; width:100%">
        <tr style="background:#f0f0f0">
            <td><b>VM Name</b></td>
            <td>{approval.vm_name}</td>
        </tr>
        <tr>
            <td><b>Action</b></td>
            <td>{ACTION_LABELS.get(approval.action,
                                   approval.action)}</td>
        </tr>
        <tr style="background:#f0f0f0">
            <td><b>Details</b></td>
            <td>{approval.action_details}</td>
        </tr>
        <tr>
            <td><b>Result</b></td>
            <td>{message}</td>
        </tr>
        <tr style="background:#f0f0f0">
            <td><b>Approver 1</b></td>
            <td>{approval.approver1_status.upper()}</td>
        </tr>
        <tr>
            <td><b>Approver 2</b></td>
            <td>{approval.approver2_status.upper()}</td>
        </tr>
        {snow_row}
    </table>
    <br>
    <a href="http://localhost:5000/approvals/"
       style="background:#0078d4; color:white;
              padding:12px 24px; text-decoration:none;
              border-radius:4px">
        View Portal
    </a>
    <p style="color:grey; font-size:12px">
        IaaS Self-Service Tool — Auto generated
    </p>
    </body></html>
    """

    for attempt in range(3):
        try:
            msg            = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = Config.MAIL_DEFAULT_SENDER
            msg['To']      = approval.requester_email
            msg.attach(MIMEText(body, 'html'))

            with smtplib.SMTP(
                Config.MAIL_SERVER,
                Config.MAIL_PORT
            ) as server:
                server.ehlo()
                server.starttls()
                server.login(
                    Config.MAIL_USERNAME,
                    Config.MAIL_PASSWORD
                )
                server.sendmail(
                    Config.MAIL_DEFAULT_SENDER,
                    approval.requester_email,
                    msg.as_string()
                )
            print(f"✅ Requester notified: "
                  f"{approval.requester_email}")
            return

        except smtplib.SMTPAuthenticationError:
            print(f"❌ Email auth failed — GoDaddy blocking")
            return

        except Exception as e:
            wait = (attempt + 1) * 5
            print(f"⚠️ Email attempt {attempt+1}/3: {e}")
            if attempt < 2:
                time.sleep(wait)

    print(f"⚠️ All email attempts failed — "
          f"action was still successful")


