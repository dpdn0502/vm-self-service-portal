import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.subscription import SubscriptionClient
from config import Config

# ── Subscription name cache (fetched once per process) ────────
_subscription_name_cache = None

# ── VM list cache ─────────────────────────────────────────────
# Avoids hitting Azure on every page load.
# TTL: 60 s — invalidated immediately after start/stop/restart.
_VM_CACHE_TTL = 60
_vm_cache = {'data': None, 'ts': 0.0}


def invalidate_vm_cache():
    """Call after any action that changes VM state."""
    _vm_cache['data'] = None
    _vm_cache['ts']   = 0.0

def _get_subscription_name() -> str:
    global _subscription_name_cache
    if _subscription_name_cache is not None:
        return _subscription_name_cache
    try:
        cred   = ClientSecretCredential(
            tenant_id     = Config.TENANT_ID,
            client_id     = Config.CLIENT_ID,
            client_secret = Config.CLIENT_SECRET,
        )
        client = SubscriptionClient(cred)
        sub    = client.subscriptions.get(Config.SUBSCRIPTION_ID)
        _subscription_name_cache = sub.display_name
    except Exception:
        _subscription_name_cache = Config.SUBSCRIPTION_ID
    return _subscription_name_cache


def get_compute_client():
    credential = ClientSecretCredential(
        tenant_id=Config.TENANT_ID,
        client_id=Config.CLIENT_ID,
        client_secret=Config.CLIENT_SECRET
    )
    return ComputeManagementClient(credential, Config.SUBSCRIPTION_ID)


def get_all_vms():
    """
    Return all VMs in the subscription with power state.

    Performance:
      • list_all() = 1 API call to enumerate VMs (names/sizes/etc.)
      • All instance_view() calls run IN PARALLEL via ThreadPoolExecutor
        so N VMs still take ~1 round-trip worth of time, not N.
      • Results cached for _VM_CACHE_TTL seconds.  Call
        invalidate_vm_cache() after any start/stop/restart action.
    """
    now = time.time()
    if (_vm_cache['data'] is not None and
            (now - _vm_cache['ts']) < _VM_CACHE_TTL):
        return _vm_cache['data']

    client            = get_compute_client()
    subscription_id   = Config.SUBSCRIPTION_ID
    subscription_name = _get_subscription_name()

    # Step 1: single call to list all VM metadata (no power state)
    raw_vms = list(client.virtual_machines.list_all())

    if not raw_vms:
        _vm_cache['data'] = []
        _vm_cache['ts']   = now
        return []

    # Step 2: fetch all instance views concurrently
    def _fetch(vm):
        rg = vm.id.split('/')[4]
        try:
            inst        = client.virtual_machines.instance_view(
                rg, vm.name
            )
            power_state = 'Unknown'
            if inst.statuses:
                for s in inst.statuses:
                    if s.code and s.code.startswith('PowerState/'):
                        power_state = s.code.replace(
                            'PowerState/', ''
                        ).title()
                        break
        except Exception:
            power_state = 'Unknown'
        return vm, rg, power_state

    max_workers = min(20, len(raw_vms))
    vm_list     = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch, vm): vm for vm in raw_vms}
        for future in as_completed(futures):
            try:
                vm, rg, power_state = future.result()
            except Exception:
                continue

            os_raw  = str(
                vm.storage_profile.os_disk.os_type
            ).lower()
            os_type = 'Linux' if 'linux' in os_raw else 'Windows'

            vm_list.append({
                'name':              vm.name,
                'resource_group':    rg,
                'location':          vm.location,
                'size':              vm.hardware_profile.vm_size,
                'power_state':       power_state,
                'os_type':           os_type,
                'subscription_id':   subscription_id,
                'subscription_name': subscription_name,
            })

    _vm_cache['data'] = vm_list
    _vm_cache['ts']   = now
    return vm_list


def get_vms_for_user(user_oid, portal_role, is_test_user=False):
    """
    Returns VMs the logged-in user has Azure RBAC access to.

    Rules (matching Azure Portal behaviour):
      - admin role          → all VMs (service principal sees all)
      - test-login user     → all VMs (dev bypass — no real OID)
      - subscription-level assignment → all VMs
      - resource-group-level assignment → VMs in those RGs
      - VM-level assignment  → only those specific VMs
      - no assignments found → empty list
    """
    # Admins and dev test-login see everything
    if portal_role == 'admin' or is_test_user:
        return get_all_vms()

    try:
        from azure.mgmt.authorization import (
            AuthorizationManagementClient
        )
        credential  = ClientSecretCredential(
            tenant_id     = Config.TENANT_ID,
            client_id     = Config.CLIENT_ID,
            client_secret = Config.CLIENT_SECRET
        )
        auth_client = AuthorizationManagementClient(
            credential, Config.SUBSCRIPTION_ID
        )
        scope = f"/subscriptions/{Config.SUBSCRIPTION_ID}"

        print(f"─── RBAC VM FILTER ─────────────────────")
        print(f"User OID:    {user_oid}")
        print(f"Portal role: {portal_role}")

        assignments = list(
            auth_client.role_assignments.list_for_scope(
                scope,
                filter=f"principalId eq '{user_oid}'"
            )
        )

        print(f"Assignments found: {len(assignments)}")

        sub_access       = False
        allowed_rgs      = set()
        allowed_vm_names = set()

        for a in assignments:
            parts = a.scope.rstrip('/').split('/')
            # /subscriptions/{id}  → 3 parts → full sub access
            if len(parts) == 3:
                sub_access = True
                break
            # /subscriptions/{id}/resourceGroups/{rg} → 5 parts
            elif (len(parts) == 5 and
                    parts[3].lower() == 'resourcegroups'):
                allowed_rgs.add(parts[4].lower())
                print(f"  RG access: {parts[4]}")
            # /subscriptions/.../virtualMachines/{name} → 9 parts
            elif (len(parts) == 9 and
                    'virtualmachines' in a.scope.lower()):
                allowed_vm_names.add(parts[-1].lower())
                print(f"  VM access: {parts[-1]}")

        print(f"────────────────────────────────────────")

        if sub_access:
            print("✅ Subscription-level access — returning all VMs")
            return get_all_vms()

        if not allowed_rgs and not allowed_vm_names:
            print("⚠️ No RBAC assignments found — returning empty list")
            return []

        all_vms = get_all_vms()
        filtered = [
            v for v in all_vms
            if v['resource_group'].lower() in allowed_rgs
            or v['name'].lower() in allowed_vm_names
        ]
        print(f"✅ Filtered to {len(filtered)}/{len(all_vms)} VMs")
        return filtered

    except Exception as e:
        print(f"⚠️ RBAC VM filter failed — falling back to all VMs: {e}")
        return get_all_vms()


def start_vm(resource_group, vm_name):
    """Start a stopped/deallocated VM"""
    client  = get_compute_client()
    poller  = client.virtual_machines.begin_start(resource_group, vm_name)
    poller.result()  # wait for completion
    return f"VM '{vm_name}' started successfully"


def stop_vm(resource_group, vm_name):
    """Stop and deallocate a VM (saves cost — no compute charges)"""
    client  = get_compute_client()
    poller  = client.virtual_machines.begin_deallocate(resource_group, vm_name)
    poller.result()
    return f"VM '{vm_name}' stopped and deallocated successfully"


def restart_vm(resource_group, vm_name):
    """Restart a running VM"""
    client  = get_compute_client()
    poller  = client.virtual_machines.begin_restart(resource_group, vm_name)
    poller.result()
    return f"VM '{vm_name}' restarted successfully"
def get_vm_info(resource_group, vm_name):
    """
    Returns OS type, admin username, and public IP for a VM.
    Used by the detail page to show OS badge and connection info.
    """
    client = get_compute_client()
    vm     = client.virtual_machines.get(resource_group, vm_name)

    # Clean OS type
    os_raw  = str(vm.storage_profile.os_disk.os_type).lower()
    os_type = 'Linux' if 'linux' in os_raw else 'Windows'

    # Admin username from OS profile
    admin_username = None
    if vm.os_profile:
        admin_username = vm.os_profile.admin_username

    # Try to get public IP via Network SDK
    public_ip = None
    try:
        from azure.mgmt.network import NetworkManagementClient
        net_client = NetworkManagementClient(
            ClientSecretCredential(
                tenant_id     = Config.TENANT_ID,
                client_id     = Config.CLIENT_ID,
                client_secret = Config.CLIENT_SECRET
            ),
            Config.SUBSCRIPTION_ID
        )

        if (vm.network_profile and
                vm.network_profile.network_interfaces):
            nic_id   = vm.network_profile.network_interfaces[0].id
            # ID format: /subscriptions/{s}/resourceGroups/{rg}/
            #            .../networkInterfaces/{name}
            parts    = nic_id.split('/')
            nic_rg   = parts[4]
            nic_name = parts[-1]

            nic = net_client.network_interfaces.get(
                nic_rg, nic_name
            )

            if nic.ip_configurations:
                pip_ref = nic.ip_configurations[0].public_ip_address
                if pip_ref:
                    pip_parts = pip_ref.id.split('/')
                    pip_rg    = pip_parts[4]
                    pip_name  = pip_parts[-1]
                    pip_obj   = net_client.public_ip_addresses.get(
                        pip_rg, pip_name
                    )
                    public_ip = pip_obj.ip_address

    except Exception as e:
        print(f"⚠️  Could not get public IP for {vm_name}: {e}")

    return {
        'os_type':        os_type,
        'admin_username': admin_username,
        'public_ip':      public_ip,
    }


def resize_vm(resource_group, vm_name, new_size):
    """Resize VM to a new SKU — requires approval first"""
    client = get_compute_client()

    # Get current VM
    vm = client.virtual_machines.get(resource_group, vm_name)

    # Update the size
    vm.hardware_profile.vm_size = new_size

    # Apply the change
    poller = client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm
    )
    poller.result()

    return f"VM '{vm_name}' resized to {new_size} successfully"