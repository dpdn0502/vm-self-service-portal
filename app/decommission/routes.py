# ═══════════════════════════════════════════════════════════════
# Decommission API — Module 16
#
# Inbound REST API called by ServiceNow (or Postman for testing).
# Authentication: Bearer token (DECOM_API_TOKEN in config).
#
# SNOW is the system of record.  This portal is the execution
# engine.  CAB approval already happened in SNOW — no dual-
# approver workflow is needed here.
# ═══════════════════════════════════════════════════════════════

from functools import wraps
from datetime import datetime

from flask import Blueprint, request, jsonify

from config import Config
from models import db, DecommissionRequest

decom_bp = Blueprint('decom', __name__, url_prefix='/api')


# ── Bearer token guard ────────────────────────────────────────

def require_api_token(f):
    """Reject requests without a valid Authorization: Bearer header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing Authorization header'}), 401
        token = auth[len('Bearer '):]
        if token != Config.DECOM_API_TOKEN:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


# ── POST /api/decommission ────────────────────────────────────

@decom_bp.route('/decommission', methods=['POST'])
@require_api_token
def create_decommission():
    """
    Queues a new VM decommission request.

    Called by ServiceNow after CAB approval, or via Postman for testing.

    Required fields: vm_name, resource_group
    Optional fields: snow_ticket, snow_sys_id, snow_caller,
                     cab_approval_number, cab_approver_name,
                     cab_approver_email, cab_approver_dept, notes

    Returns 201 with decom_id and status_url.
    Returns 400 if required fields missing.
    Returns 409 if an active decom already exists for the same VM.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'JSON body required'}), 400

    vm_name        = (data.get('vm_name') or '').strip()
    resource_group = (data.get('resource_group') or '').strip()

    if not vm_name or not resource_group:
        return jsonify({
            'error': 'vm_name and resource_group are required'
        }), 400

    # Reject if an active (non-terminal) decom already exists for this VM
    active_states = ['pending', 'prechecks_running',
                     'prechecks_done', 'soft_deleted']
    existing = DecommissionRequest.query.filter(
        DecommissionRequest.vm_name        == vm_name,
        DecommissionRequest.resource_group == resource_group,
        DecommissionRequest.state.in_(active_states)
    ).first()
    if existing:
        return jsonify({
            'error':    f'Active decommission already exists for {vm_name}',
            'decom_id': existing.id,
            'state':    existing.state,
        }), 409

    # Build the record
    decom = DecommissionRequest(
        vm_name             = vm_name,
        resource_group      = resource_group,
        subscription_id     = Config.SUBSCRIPTION_ID,
        snow_ticket         = data.get('snow_ticket', '').strip() or None,
        snow_sys_id         = data.get('snow_sys_id', '').strip() or None,
        snow_caller         = data.get('snow_caller', '').strip() or None,
        cab_approval_number = data.get('cab_approval_number', '').strip() or None,
        cab_approver_name   = data.get('cab_approver_name', '').strip() or None,
        cab_approver_email  = data.get('cab_approver_email', '').strip() or None,
        cab_approver_dept   = data.get('cab_approver_dept', '').strip() or None,
        notes               = data.get('notes', '').strip() or None,
        state               = 'pending',
        initiated_by        = 'SNOW API',
    )
    db.session.add(decom)
    db.session.commit()

    # Send "queued" notification if email present
    try:
        from app.decommission.decom_service import send_decom_notification
        send_decom_notification(decom, 'queued')
    except Exception as e:
        print(f'⚠️  Decom notification failed (queued): {e}')

    print(f'✅ Decommission #{decom.id} queued: '
          f'{vm_name} ({resource_group}) — '
          f'ticket: {decom.snow_ticket or "none"}')

    return jsonify({
        'success':    True,
        'decom_id':   decom.id,
        'message':    (
            f'Decommission request queued for {vm_name}'
            + (f' ({decom.snow_ticket})' if decom.snow_ticket else '')
        ),
        'status_url': f'/api/decommission/{decom.id}/status',
    }), 201


# ── GET /api/decommission/<id>/status ────────────────────────

@decom_bp.route('/decommission/<int:decom_id>/status', methods=['GET'])
@require_api_token
def decom_status(decom_id):
    """
    Returns current state + key fields for the given decom request.
    Used by SNOW to poll progress without logging into the portal.
    """
    decom = DecommissionRequest.query.get(decom_id)
    if not decom:
        return jsonify({'error': f'Decommission #{decom_id} not found'}), 404

    return jsonify({
        'decom_id':       decom.id,
        'vm_name':        decom.vm_name,
        'resource_group': decom.resource_group,
        'snow_ticket':    decom.snow_ticket,
        'state':          decom.state,
        'updated_at':     decom.updated_at.strftime('%Y-%m-%d %H:%M:%S') if decom.updated_at else None,
        'soft_deleted_at':decom.soft_deleted_at.strftime('%Y-%m-%d %H:%M:%S') if decom.soft_deleted_at else None,
        'hard_delete_due':decom.hard_delete_due.strftime('%Y-%m-%d') if decom.hard_delete_due else None,
        'completed_at':   decom.completed_at.strftime('%Y-%m-%d %H:%M:%S') if decom.completed_at else None,
        'error_message':  decom.error_message,
    })
