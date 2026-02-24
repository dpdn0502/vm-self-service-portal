from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from config import Config


def get_compute_client():
    credential = ClientSecretCredential(
        tenant_id=Config.TENANT_ID,
        client_id=Config.CLIENT_ID,
        client_secret=Config.CLIENT_SECRET
    )
    return ComputeManagementClient(credential, Config.SUBSCRIPTION_ID)


def get_all_vms():
    client  = get_compute_client()
    vm_list = []

    vms = client.virtual_machines.list_all()

    for vm in vms:
        resource_group = vm.id.split('/')[4]

        instance    = client.virtual_machines.instance_view(
            resource_group, vm.name
        )

        power_state = "Unknown"
        if instance.statuses:
            for status in instance.statuses:
                if status.code.startswith('PowerState/'):
                    power_state = status.code.replace('PowerState/', '').title()
                    break

        vm_list.append({
            'name':           vm.name,
            'resource_group': resource_group,
            'location':       vm.location,
            'size':           vm.hardware_profile.vm_size,
            'power_state':    power_state,
            'os_type':        str(vm.storage_profile.os_disk.os_type)
        })

    return vm_list


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