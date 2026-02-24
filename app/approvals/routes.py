from flask import (Blueprint, render_template, session,
                   redirect, url_for, request, jsonify)
from models import ApprovalRequest
from app.approvals.approval_service import (
    create_approval_request,
    process_approval_decision
)
from functools import wraps
from datetime import datetime

approvals_bp = Blueprint('approvals', __name__, url_prefix='/approvals')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('auth.login',
                                    next=request.url))
        return f(*args, **kwargs)
    return decorated


@approvals_bp.route('/')
@login_required
def approval_queue():
    user       = session.get('user')
    user_email = user.get('preferred_username')

    # DEBUG
    print(f"─── APPROVAL QUEUE DEBUG ───────────────")
    print(f"Logged in as: {user_email}")

    pending = ApprovalRequest.query.filter_by(
        status='pending'
    ).order_by(ApprovalRequest.created_at.desc()).all()

    print(f"Total pending requests: {len(pending)}")
    for p in pending:
        print(f"Request #{p.id}:")
        print(f"  approver1_azure: '{p.approver1_azure}'")
        print(f"  approver2_azure: '{p.approver2_azure}'")
        print(f"  Match1: {user_email == p.approver1_azure}")
        print(f"  Match2: {user_email == p.approver2_azure}")

    to_approve = ApprovalRequest.query.filter(
        ApprovalRequest.status == 'pending'
    ).filter(
        (ApprovalRequest.approver1_azure == user_email) |
        (ApprovalRequest.approver2_azure == user_email)
    ).all()

    print(f"Requests needing my approval: {len(to_approve)}")
    print(f"────────────────────────────────────────")

    my_requests = ApprovalRequest.query.filter_by(
        requester_email=user_email
    ).order_by(ApprovalRequest.created_at.desc()).all()

    return render_template(
        'approvals.html',
        user=user,
        pending=pending,
        my_requests=my_requests,
        to_approve=to_approve
    )


@approvals_bp.route('/debug/<int:approval_id>')
@login_required
def debug_approval(approval_id):
    """Temporary debug route"""
    user     = session.get('user')
    approval = ApprovalRequest.query.get_or_404(approval_id)

    return jsonify({
        'id':                approval.id,
        'status':            approval.status,
        'approver1_azure':   approval.approver1_azure,
        'approver2_azure':   approval.approver2_azure,
        'approver1_email':   approval.approver1_email,
        'approver2_email':   approval.approver2_email,
        'approver1_status':  approval.approver1_status,
        'approver2_status':  approval.approver2_status,
        'your_azure_login':  user.get('preferred_username'),
        'your_name':         user.get('name'),
        'you_are_approver1': (
            user.get('preferred_username') == approval.approver1_azure
        ),
        'you_are_approver2': (
            user.get('preferred_username') == approval.approver2_azure
        ),
        'action':            approval.action,
        'action_details':    approval.action_details,
        'vm_name':           approval.vm_name,
        'resource_group':    approval.resource_group
    })


@approvals_bp.route('/debug/query')
@login_required
def debug_query():
    user       = session.get('user')
    user_email = user.get('preferred_username')

    all_pending = ApprovalRequest.query.filter_by(
        status='pending'
    ).all()

    to_approve = ApprovalRequest.query.filter(
        ApprovalRequest.status == 'pending'
    ).filter(
        (ApprovalRequest.approver1_azure == user_email) |
        (ApprovalRequest.approver2_azure == user_email)
    ).all()

    return jsonify({
        'your_email':      user_email,
        'total_pending':   len(all_pending),
        'your_to_approve': len(to_approve),
        'pending_details': [
            {
                'id':              p.id,
                'approver1_azure': p.approver1_azure,
                'approver2_azure': p.approver2_azure,
                'status':          p.status
            }
            for p in all_pending
        ]
    })


@approvals_bp.route('/status/<int:approval_id>')
@login_required
def approval_status(approval_id):
    """Returns current status — used by frontend polling"""
    approval = ApprovalRequest.query.get_or_404(approval_id)

    elapsed  = int(
        (datetime.utcnow() - approval.created_at).total_seconds()
    )

    messages = {
        'pending':  'Waiting for approvals',
        'approved': 'Both approved — executing VM resize',
        'executed': 'VM resize completed successfully',
        'rejected': 'Request was rejected',
        'failed':   'Execution failed'
    }

    return jsonify({
        'status':           approval.status,
        'message':          messages.get(
                                approval.status,
                                approval.status
                            ),
        'elapsed':          elapsed,
        'approver1_status': approval.approver1_status,
        'approver2_status': approval.approver2_status
    })


@approvals_bp.route('/<int:approval_id>')
@login_required
def approval_detail(approval_id):
    user     = session.get('user')
    approval = ApprovalRequest.query.get_or_404(approval_id)
    return render_template(
        'approval_detail.html',
        user=user,
        approval=approval
    )


@approvals_bp.route('/<int:approval_id>/decide', methods=['POST'])
@login_required
def decide(approval_id):
    user           = session.get('user')
    approver_email = user.get('preferred_username')
    decision       = request.form.get('decision')
    comment        = request.form.get('comment', '')

    print(f"Decision submitted by: {approver_email}")
    print(f"Decision: {decision}")
    print(f"Approval ID: {approval_id}")

    result = process_approval_decision(
        approval_id,
        approver_email,
        decision,
        comment
    )

    return jsonify(result)


@approvals_bp.route('/request', methods=['POST'])
@login_required
def request_approval():
    user           = session.get('user')
    vm_name        = request.form.get('vm_name')
    resource_group = request.form.get('resource_group')
    action         = request.form.get('action')
    action_details = request.form.get('action_details')

    print(f"─── NEW APPROVAL REQUEST ───────────────")
    print(f"User:    {user.get('preferred_username')}")
    print(f"VM:      {vm_name}")
    print(f"Action:  {action}")
    print(f"Details: {action_details}")
    print(f"────────────────────────────────────────")

    approval = create_approval_request(
        user, vm_name, resource_group,
        action, action_details
    )

    return jsonify({
        'success':     True,
        'approval_id': approval.id,
        'snow_ticket': approval.snow_ticket,
        'message':     (
            f"Approval request #{approval.id} submitted. "
            f"Both approvers notified. "
            f"Ticket: {approval.snow_ticket or 'Pending'}"
        )
    })