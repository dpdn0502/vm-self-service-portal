from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    DataDisk,
    ManagedDiskParameters,
    DiskCreateOptionTypes
)
from config import Config


def get_compute_client():
    credential = ClientSecretCredential(
        tenant_id=Config.TENANT_ID,
        client_id=Config.CLIENT_ID,
        client_secret=Config.CLIENT_SECRET
    )
    return ComputeManagementClient(credential, Config.SUBSCRIPTION_ID)


def get_vm_disks(resource_group, vm_name):
    """
    Returns all disks attached to a VM
    including OS disk and data disks
    """
    client = get_compute_client()
    vm     = client.virtual_machines.get(
        resource_group, vm_name
    )

    disks = []

    # OS Disk
    os_disk = vm.storage_profile.os_disk
    disks.append({
        'name':         os_disk.name,
        'type':         'OS Disk',
        'lun':          None,
        'disk_id':      os_disk.managed_disk.id
                        if os_disk.managed_disk else None,
        'caching':      str(os_disk.caching),
        'size_gb':      os_disk.disk_size_gb or 'N/A',
        'can_detach':   False  # OS disk cannot be detached
    })

    # Data Disks
    for disk in vm.storage_profile.data_disks:
        disks.append({
            'name':       disk.name,
            'type':       'Data Disk',
            'lun':        disk.lun,
            'disk_id':    disk.managed_disk.id
                          if disk.managed_disk else None,
            'caching':    str(disk.caching),
            'size_gb':    disk.disk_size_gb or 'N/A',
            'can_detach': True
        })

    return disks


def get_available_disks(resource_group):
    """
    Returns unattached disks available to attach
    in the same resource group
    """
    client           = get_compute_client()
    available_disks  = []

    all_disks = client.disks.list_by_resource_group(
        resource_group
    )

    for disk in all_disks:
        # Only show unattached disks
        if disk.disk_state == 'Unattached':
            available_disks.append({
                'name':     disk.name,
                'size_gb':  disk.disk_size_gb,
                'sku':      disk.sku.name if disk.sku else 'N/A',
                'id':       disk.id,
                'location': disk.location
            })

    return available_disks


def attach_disk(resource_group, vm_name, disk_name, disk_id):
    """
    Attaches an existing unattached disk to a VM
    Requires approval before calling this function
    """
    client = get_compute_client()
    vm     = client.virtual_machines.get(
        resource_group, vm_name
    )

    # Find next available LUN
    existing_luns = [
        d.lun for d in vm.storage_profile.data_disks
    ]
    lun = 0
    while lun in existing_luns:
        lun += 1

    print(f"Attaching {disk_name} at LUN {lun}")

    # Add disk to VM
    vm.storage_profile.data_disks.append(
        DataDisk(
            lun=lun,
            name=disk_name,
            create_option=DiskCreateOptionTypes.ATTACH,
            managed_disk=ManagedDiskParameters(id=disk_id)
        )
    )

    poller = client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm
    )
    poller.result()

    return f"Disk '{disk_name}' attached to '{vm_name}' at LUN {lun}"


def detach_disk(resource_group, vm_name, disk_name):
    """
    Detaches a data disk from a VM
    Disk is NOT deleted — just unattached
    Requires approval before calling this function
    """
    client = get_compute_client()
    vm     = client.virtual_machines.get(
        resource_group, vm_name
    )

    # Find and remove the disk
    original_count = len(vm.storage_profile.data_disks)
    vm.storage_profile.data_disks = [
        d for d in vm.storage_profile.data_disks
        if d.name != disk_name
    ]

    if len(vm.storage_profile.data_disks) == original_count:
        raise Exception(
            f"Disk '{disk_name}' not found on VM '{vm_name}'"
        )

    print(f"Detaching {disk_name} from {vm_name}")

    poller = client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm
    )
    poller.result()

    return f"Disk '{disk_name}' detached from '{vm_name}' successfully"