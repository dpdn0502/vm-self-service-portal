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

    # ServiceNow fields — NEW
    snow_ticket      = db.Column(db.String(20))    # INC0012345
    snow_ticket_url  = db.Column(db.String(500))   # link to ticket

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