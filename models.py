from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

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
