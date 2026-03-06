# ═══════════════════════════════════════════════════════════════
# RBAC Service — Role Based Access Control
# Module 10 — Azure RBAC Integration
# ═══════════════════════════════════════════════════════════════

from azure.identity import ClientSecretCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from config import Config
import functools
from flask import session, jsonify, redirect, url_for
PORTAL_ADMINS = [
    'dinesh.birisetti@coe.ctsmbg.cloud', # corporate admin entra id bust be replaced later  
    'dineshch.babu@gmail.com',    # breakglassbust replace accounts for testing 
   
]

# ── Role Definitions ──────────────────────────────────────────
# Maps Azure built-in role names to portal roles
# Industry Standard: Least privilege by default

AZURE_ROLE_MAP = {
    # Full access roles → Admin
    'Owner':                        'admin',
    'Contributor':                  'admin',

    # VM specific roles → Contributor
    'Virtual Machine Contributor':  'contributor',

    # VM operator role → Operator
    'Virtual Machine Operator':     'operator',

    # Read only → Reader
    'Reader':                       'reader',
    'Virtual Machine Reader':       'reader'
}

# ── Permission Matrix ─────────────────────────────────────────
# Defines exactly what each role can do

PERMISSIONS = {
    'admin': [
        'view_vms',
        'start', 'stop', 'restart',
        'resize',
        'disk_attach', 'disk_detach',
        'snapshot_create', 'snapshot_delete',
        'view_audit', 'export_audit',
        'manage_approvals',
        'view_tags', 'edit_tags'
    ],
    'contributor': [
        'view_vms',
        'start', 'stop', 'restart',
        'resize',
        'disk_attach', 'disk_detach',
        'snapshot_create', 'snapshot_delete',
        'view_audit',
        'view_tags', 'edit_tags'
    ],
    'operator': [
        'view_vms',
        'start', 'stop', 'restart',
        'snapshot_create',
        'view_audit',
        'view_tags'
    ],
    'reader': [
        'view_vms',
        'view_audit',
        'view_tags'
    ]
}


# ── Get User Role from Azure ──────────────────────────────────

def get_user_portal_role(user_object_id,
                          user_email=None):
    """
    Queries Azure RBAC to get user portal role
    Checks hardcoded admins first as break-glass
    Then falls back to Azure RBAC lookup
    """

    # ── Break-glass admin check first ────────────────────────
    if user_email and user_email.lower() \
       in [a.lower() for a in PORTAL_ADMINS]:
        print(f"[OK] Hardcoded admin: {user_email}")
        return 'admin'

    # ── Azure RBAC lookup ─────────────────────────────────────
    try:
        credential = ClientSecretCredential(
            tenant_id     = Config.TENANT_ID,
            client_id     = Config.CLIENT_ID,
            client_secret = Config.CLIENT_SECRET
        )

        auth_client = AuthorizationManagementClient(
            credential,
            Config.SUBSCRIPTION_ID
        )

        scope = (
            f"/subscriptions/{Config.SUBSCRIPTION_ID}"
        )

        # Get all role assignments for this user
        assignments = auth_client.role_assignments\
            .list_for_scope(
                scope,
                filter=f"principalId eq "
                       f"'{user_object_id}'"
            )

        highest_role = 'reader'

        for assignment in assignments:
            role_def_id = (
                assignment.role_definition_id
                .split('/')[-1]
            )

            role_def = auth_client.role_definitions.get(
                scope, role_def_id
            )

            role_name   = role_def.role_name
            portal_role = AZURE_ROLE_MAP.get(
                role_name, None
            )

            print(f"─── RBAC CHECK ─────────────────────")
            print(f"Azure Role:  {role_name}")
            print(f"Portal Role: {portal_role}")
            print(f"────────────────────────────────────")

            if portal_role:
                highest_role = elevate_role(
                    highest_role, portal_role
                )

        print(f"[OK] Final portal role: {highest_role}")
        return highest_role

    except Exception as e:
        print(f"[WARN] RBAC check failed: {e}")
        print(f"Defaulting to reader role")
        return 'reader'

        # Get all role assignments for this user
        assignments = auth_client.role_assignments.list_for_scope(
                                           scope,
        filter=f"principalId eq '{user_object_id}'"
        )

        highest_role = 'reader'  # Default — least privilege

        for assignment in assignments:
            # Get role definition name
            role_def_id = (
                assignment.role_definition_id.split('/')[-1]
            )

            role_def = auth_client.role_definitions.get(
                scope, role_def_id
            )

            role_name    = role_def.role_name
            portal_role  = AZURE_ROLE_MAP.get(
                role_name, None
            )

            print(f"─── RBAC CHECK ─────────────────────")
            print(f"Azure Role:  {role_name}")
            print(f"Portal Role: {portal_role}")
            print(f"────────────────────────────────────")

            # Elevate to highest role found
            if portal_role:
                highest_role = elevate_role(
                    highest_role, portal_role
                )

        print(f"[OK] Final portal role: {highest_role}")
        return highest_role

    except Exception as e:
        print(f"[WARN] RBAC check failed: {e}")
        print(f"Defaulting to reader role")
        return 'reader'  # Fail safe — least privilege


def elevate_role(current, new_role):
    """
    Returns highest role between current and new
    Role hierarchy: admin > contributor > operator > reader
    """
    hierarchy = ['reader', 'operator',
                 'contributor', 'admin']

    current_level = hierarchy.index(current) \
                    if current in hierarchy else 0
    new_level     = hierarchy.index(new_role) \
                    if new_role in hierarchy else 0

    return hierarchy[max(current_level, new_level)]


# ── Permission Check ──────────────────────────────────────────

def has_permission(permission):
    """
    Checks if current user has a specific permission
    Uses role stored in session
    """
    role = session.get('portal_role', 'reader')
    return permission in PERMISSIONS.get(role, [])


def get_session_role():
    """Returns current user role from session"""
    return session.get('portal_role', 'reader')


# ── Route Decorators ──────────────────────────────────────────

def require_permission(permission):
    """
    Decorator for routes requiring specific permission
    Usage: @require_permission('resize')
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('auth.login'))

            if not has_permission(permission):
                role = get_session_role()
                return jsonify({
                    'status':  'error',
                    'message': (
                        f'Access denied. Your role '
                        f'({role}) does not have '
                        f'permission to perform '
                        f'this action.'
                    )
                }), 403

            return f(*args, **kwargs)
        return decorated
    return decorator


def require_role(minimum_role):
    """
    Decorator requiring minimum role level
    Usage: @require_role('contributor')
    """
    hierarchy = ['reader', 'operator',
                 'contributor', 'admin']

    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('auth.login'))

            current_role  = get_session_role()
            current_level = hierarchy.index(
                                current_role
                            ) if current_role \
                              in hierarchy else 0
            required_level = hierarchy.index(
                                 minimum_role
                             ) if minimum_role \
                               in hierarchy else 0

            if current_level < required_level:
                return jsonify({
                    'status':  'error',
                    'message': (
                        f'Access denied. Requires '
                        f'{minimum_role} role or higher. '
                        f'Your role: {current_role}'
                    )
                }), 403

            return f(*args, **kwargs)
        return decorated
    return decorator
