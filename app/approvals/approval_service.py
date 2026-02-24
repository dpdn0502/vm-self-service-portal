import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from config import Config
from models import db, ApprovalRequest
from app.servicenow.snow_service import create_incident


# Risk classification
RISK_LEVELS = {
    'resize':          'medium',
    'disk_attach':     'medium',
    'disk_detach':     'medium',
    'os_disk_swap':    'high',
    'backup':          'high',
    'snapshot_create': 'medium',
    'snapshot_delete': 'high'
}

# Human readable action names
ACTION_LABELS = {
    'resize':          'Resize VM (SKU Change)',
    'disk_attach':     'Attach Data Disk',
    'disk_detach':     'Detach Data Disk',
    'os_disk_swap':    'OS Disk Swap',
    'backup':          'Backup Management',
    'snapshot_create': 'Create Disk Snapshot',
    'snapshot_delete': 'Delete Disk Snapshot'
}


def create_approval_request(user, vm_name, resource_group,
                             action, action_details):
    """
    Creates approval request in database
    Notifies both approvers via email and ServiceNow
    """
    risk_level = RISK_LEVELS.get(action, 'medium')

    approval = ApprovalRequest(
        requester_email  = user.get('preferred_username'),
        requester_name   = user.get('name'),
        vm_name          = vm_name,
        resource_group   = resource_group,
        action           = action,
        action_details   = action_details,
        risk_level       = risk_level,
        status           = 'pending',

        # Notification emails
        approver1_email  = Config.APPROVER1_EMAIL,
        approver2_email  = Config.APPROVER2_EMAIL,

        # Azure login emails for portal access check
        approver1_azure  = Config.APPROVER1_AZURE,
        approver2_azure  = Config.APPROVER2_AZURE,

        approver1_status = 'pending',
        approver2_status = 'pending'
    )
    db.session.add(approval)
    db.session.commit()

    # Notify approvers via email
    try:
        send_approval_email(approval)
    except Exception as e:
        print(f"⚠️ Email failed but approval created: {e}")

    # Create ServiceNow ticket
    try:
        snow_result = create_incident(
            vm_name        = vm_name,
            action         = f"APPROVAL REQUEST - {action}",
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

        if snow_result['success']:
            approval.snow_ticket     = snow_result['incident_number']
            approval.snow_ticket_url = snow_result['incident_url']
            db.session.commit()

    except Exception as e:
        print(f"⚠️ ServiceNow failed but approval created: {e}")

    return approval


def send_approval_email(approval):
    """Sends email notification to both approvers"""
    if not Config.MAIL_USERNAME:
        print("Email not configured — skipping")
        return

    subject = (
        f"[APPROVAL REQUIRED] "
        f"{ACTION_LABELS.get(approval.action, approval.action)} "
        f"— {approval.vm_name}"
    )

    body = f"""
    <html><body>
    <h2 style="color:#0078d4">⚠️ VM Action Approval Required</h2>
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
        VM Self-Service Portal — Auto generated
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

                with smtplib.SMTP(Config.MAIL_SERVER,
                                  Config.MAIL_PORT) as server:
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
                               decision, comment):
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

    print(f"─── APPROVAL DECISION ──────────────────")
    print(f"Approver:    {approver_email}")
    print(f"Decision:    {decision}")
    print(f"Approver1:   {approval.approver1_azure}")
    print(f"Approver2:   {approval.approver2_azure}")
    print(f"────────────────────────────────────────")

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

    if not decision_made:
        return {
            'success': False,
            'error':   'You are not an approver or already decided'
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
        'message': 'Decision recorded — waiting for second approver'
    }


def execute_approved_action(approval):
    """Executes Azure action after both approvals"""
    from app.vm.azure_service import resize_vm
    from app.vm.disk_service import attach_disk, detach_disk
    from app.vm.snapshot_service import (
        create_snapshot,
        delete_snapshot
    )

    print(f"─── EXECUTING ACTION ───────────────────")
    print(f"Action:  {approval.action}")
    print(f"Details: {approval.action_details}")
    print(f"VM:      {approval.vm_name}")
    print(f"RG:      {approval.resource_group}")
    print(f"────────────────────────────────────────")

    try:
        # Parse action details into key value pairs
        details = {}
        for part in approval.action_details.split('|'):
            if ':' in part:
                key, val = part.split(':', 1)
                details[key.strip()] = val.strip()

        print(f"Parsed details: {details}")

        # ── Resize ───────────────────────────────────────────
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

        else:
            message = f"Action {approval.action} executed"

        approval.status = 'executed'
        db.session.commit()

        # Create completion ServiceNow ticket
        try:
            create_incident(
                vm_name        = approval.vm_name,
                action         = approval.action,
                user_name      = approval.requester_name,
                user_email     = approval.requester_email,
                resource_group = approval.resource_group,
                status         = 'success',
                message        = f"Approved action executed: {message}"
            )
        except Exception as e:
            print(f"⚠️ ServiceNow notification failed: {e}")

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
        'executed': '✅ Your request was approved and executed'
    }.get(status, status)

    subject = (
        f"[VM Portal] {status_text} — "
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
        VM Self-Service Portal — Auto generated
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

            with smtplib.SMTP(Config.MAIL_SERVER,
                              Config.MAIL_PORT) as server:
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
