from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    Snapshot,
    SnapshotSku,
    CreationData,
    DiskCreateOption
)
from config import Config
from datetime import datetime


def get_compute_client():
    credential = ClientSecretCredential(
        tenant_id=Config.TENANT_ID,
        client_id=Config.CLIENT_ID,
        client_secret=Config.CLIENT_SECRET
    )
    return ComputeManagementClient(credential, Config.SUBSCRIPTION_ID)


def get_disk_id(resource_group, vm_name, disk_name):
    """Gets the resource ID of a specific disk"""
    client = get_compute_client()
    vm     = client.virtual_machines.get(
        resource_group, vm_name
    )

    # Check OS disk
    if vm.storage_profile.os_disk.name == disk_name:
        return vm.storage_profile.os_disk.managed_disk.id

    # Check data disks
    for disk in vm.storage_profile.data_disks:
        if disk.name == disk_name:
            return disk.managed_disk.id

    raise Exception(f"Disk '{disk_name}' not found on VM")


def create_snapshot(resource_group, vm_name,
                    disk_name, snapshot_name=None):
    """
    Creates a snapshot of a VM disk
    Works on both OS disk and data disks
    """
    client = get_compute_client()

    # Auto generate snapshot name if not provided
    if not snapshot_name:
        timestamp     = datetime.utcnow().strftime('%Y%m%d-%H%M')
        snapshot_name = f"snap-{disk_name}-{timestamp}"

    # Get disk ID
    disk_id = get_disk_id(resource_group, vm_name, disk_name)

    print(f"Creating snapshot: {snapshot_name}")
    print(f"Source disk ID:    {disk_id}")

    snapshot = Snapshot(
        location=get_vm_location(resource_group, vm_name),
        creation_data=CreationData(
            create_option=DiskCreateOption.COPY,
            source_resource_id=disk_id
        ),
        sku=SnapshotSku(name='Standard_LRS')
    )

    poller = client.snapshots.begin_create_or_update(
        resource_group,
        snapshot_name,
        snapshot
    )
    result = poller.result()

    return {
        'name':       result.name,
        'id':         result.id,
        'size_gb':    result.disk_size_gb,
        'created_at': result.time_created.strftime(
                          '%Y-%m-%d %H:%M:%S'
                      ) if result.time_created else 'N/A'
    }


def get_snapshots(resource_group):
    """Lists all snapshots in a resource group"""
    client    = get_compute_client()
    snapshots = []

    for snap in client.snapshots.list_by_resource_group(
        resource_group
    ):
        snapshots.append({
            'name':       snap.name,
            'size_gb':    snap.disk_size_gb,
            'created_at': snap.time_created.strftime(
                              '%Y-%m-%d %H:%M:%S'
                          ) if snap.time_created else 'N/A',
            'source':     snap.creation_data.source_resource_id
                          if snap.creation_data else 'N/A',
            'state':      snap.disk_state or 'N/A'
        })

    return snapshots


def delete_snapshot(resource_group, snapshot_name):
    """Deletes a snapshot permanently"""
    client = get_compute_client()

    print(f"Deleting snapshot: {snapshot_name}")

    poller = client.snapshots.begin_delete(
        resource_group,
        snapshot_name
    )
    poller.result()

    return f"Snapshot '{snapshot_name}' deleted successfully"


def get_vm_location(resource_group, vm_name):
    """Helper — gets VM location for snapshot creation"""
    client = get_compute_client()
    vm     = client.virtual_machines.get(
        resource_group, vm_name
    )
    return vm.location