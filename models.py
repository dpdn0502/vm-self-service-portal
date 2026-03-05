from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

db = SQLAlchemy()


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id               = db.Column(db.Integer, primary_key=True)
    timestamp        = db.Column(db.DateTime, default=datetime.utcnow)
    user_email       = db.Column(db.String(100))
    user_name        = db.Column(db.String(100))
    vm_name          = db.Column(db.String(100))
    resource_group   = db.Column(db.String(100))
    action           = db.Column(db.String(50))
    status           = db.Column(db.String(20))
    message          = db.Column(db.String(500))
    subscription     = db.Column(db.String(100))
    snow_ticket      = db.Column(db.String(20))
    snow_ticket_url  = db.Column(db.String(500))

    def to_dict(self):
        return {
            'id':              self.id,
            'timestamp':       self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'user_email':      self.user_email,
            'user_name':       self.user_name,
            'vm_name':         self.vm_name,
            'resource_group':  self.resource_group,
            'action':          self.action,
            'status':          self.status,
            'message':         self.message,
            'snow_ticket':     self.snow_ticket,
            'snow_ticket_url': self.snow_ticket_url
        }


class ApprovalRequest(db.Model):
    __tablename__ = 'approval_requests'

    id               = db.Column(db.Integer, primary_key=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow,
                                 onupdate=datetime.utcnow)
     # ── ADD THESE TWO LINES for CR, INC and SR type───────────────────────────────────
    snow_sys_id      = db.Column(db.String(100))
    snow_ticket_type = db.Column(db.String(50))
    # ────────────────────────────────────────────────

    # Requester details
    requester_email  = db.Column(db.String(100))
    requester_name   = db.Column(db.String(100))

    # VM details
    vm_name          = db.Column(db.String(100))
    resource_group   = db.Column(db.String(100))
    action           = db.Column(db.String(50))
    action_details   = db.Column(db.String(500))
    risk_level       = db.Column(db.String(20))

    # Overall status
    # pending, approved, rejected, executed, failed
    status           = db.Column(db.String(20), default='pending')

    # Approver 1
    approver1_email  = db.Column(db.String(100))  # real inbox
    approver1_azure  = db.Column(db.String(100))  # azure login
    approver1_name   = db.Column(db.String(100))
    approver1_status = db.Column(db.String(20), default='pending')
    approver1_comment= db.Column(db.String(500))
    approver1_at     = db.Column(db.DateTime)

    # Approver 2
    approver2_email  = db.Column(db.String(100))  # real inbox
    approver2_azure  = db.Column(db.String(100))  # azure login
    approver2_name   = db.Column(db.String(100))
    approver2_status = db.Column(db.String(20), default='pending')
    approver2_comment= db.Column(db.String(500))
    approver2_at     = db.Column(db.DateTime)

    # ServiceNow
    snow_ticket      = db.Column(db.String(20))
    snow_ticket_url  = db.Column(db.String(500))

    def both_approved(self):
        return (self.approver1_status == 'approved' and
                self.approver2_status == 'approved')

    def either_rejected(self):
        return (self.approver1_status == 'rejected' or
                self.approver2_status == 'rejected')

    def to_dict(self):
        return {
            'id':               self.id,
            'created_at':       self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'requester_name':   self.requester_name,
            'requester_email':  self.requester_email,
            'vm_name':          self.vm_name,
            'resource_group':   self.resource_group,
            'action':           self.action,
            'action_details':   self.action_details,
            'risk_level':       self.risk_level,
            'status':           self.status,
            'approver1_status': self.approver1_status,
            'approver2_status': self.approver2_status,
            'snow_ticket':      self.snow_ticket
        }


class DecommissionRequest(db.Model):
    """
    Tracks the full lifecycle of a SNOW-initiated VM decommission.
    States: pending → prechecks_running → prechecks_done → soft_deleted
            → hard_deleted | restored | prechecks_failed
    """
    __tablename__ = 'decommission_requests'

    id              = db.Column(db.Integer, primary_key=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow,
                                onupdate=datetime.utcnow)

    # VM identification
    vm_name         = db.Column(db.String(100), nullable=False)
    resource_group  = db.Column(db.String(100), nullable=False)
    subscription_id = db.Column(db.String(100))

    # SNOW data (from inbound API call)
    snow_ticket     = db.Column(db.String(50))   # CHG number
    snow_sys_id     = db.Column(db.String(100))  # for PATCH updates back to SNOW
    snow_caller     = db.Column(db.String(100))  # who raised the ticket

    # CAB approval reference
    cab_approval_number = db.Column(db.String(100))
    cab_approver_name   = db.Column(db.String(100))
    cab_approver_email  = db.Column(db.String(100))  # if present → phase emails sent
    cab_approver_dept   = db.Column(db.String(100))

    # State machine
    state           = db.Column(db.String(30), default='pending')

    # Pre-check results (captured synchronously during prechecks)
    snapshot_name   = db.Column(db.String(200))
    snapshot_id     = db.Column(db.String(500))
    metadata_json   = db.Column(db.Text)   # full VM config as JSON
    dns_records     = db.Column(db.Text)   # DNS config as text
    precheck_notes  = db.Column(db.Text)   # warnings / observations

    # Soft-delete tracking
    soft_deleted_at   = db.Column(db.DateTime)
    hard_delete_due   = db.Column(db.DateTime)  # advisory — soft_deleted_at + 30 days

    # Completion
    completed_at    = db.Column(db.DateTime)
    completed_by    = db.Column(db.String(100))  # admin email

    # Metadata
    initiated_by    = db.Column(db.String(100))  # 'SNOW API' or admin email
    notes           = db.Column(db.Text)
    error_message   = db.Column(db.Text)

    # ── Helpers ────────────────────────────────────────────────

    def days_until_due(self):
        if not self.hard_delete_due:
            return None
        return (self.hard_delete_due - datetime.utcnow()).days

    def is_overdue(self):
        if not self.hard_delete_due:
            return False
        return datetime.utcnow() > self.hard_delete_due

    def to_dict(self):
        return {
            'id':                   self.id,
            'created_at':           self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at':           self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None,
            'vm_name':              self.vm_name,
            'resource_group':       self.resource_group,
            'subscription_id':      self.subscription_id,
            'snow_ticket':          self.snow_ticket,
            'snow_sys_id':          self.snow_sys_id,
            'snow_caller':          self.snow_caller,
            'cab_approval_number':  self.cab_approval_number,
            'cab_approver_name':    self.cab_approver_name,
            'cab_approver_email':   self.cab_approver_email,
            'cab_approver_dept':    self.cab_approver_dept,
            'state':                self.state,
            'snapshot_name':        self.snapshot_name,
            'metadata_json':        self.metadata_json,
            'dns_records':          self.dns_records,
            'precheck_notes':       self.precheck_notes,
            'soft_deleted_at':      self.soft_deleted_at.strftime('%Y-%m-%d %H:%M:%S') if self.soft_deleted_at else None,
            'hard_delete_due':      self.hard_delete_due.strftime('%Y-%m-%d') if self.hard_delete_due else None,
            'completed_at':         self.completed_at.strftime('%Y-%m-%d %H:%M:%S') if self.completed_at else None,
            'completed_by':         self.completed_by,
            'initiated_by':         self.initiated_by,
            'notes':                self.notes,
            'error_message':        self.error_message,
            'days_until_due':       self.days_until_due(),
            'is_overdue':           self.is_overdue(),
        }
