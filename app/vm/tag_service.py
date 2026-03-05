# ═══════════════════════════════════════════════════════════════
# Tag Service — Azure VM Tag Management
# Module 12 — CAF-compliant tag operations
# ═══════════════════════════════════════════════════════════════

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from config import Config


# CAF standard tag schema — keys and suggested values
# Source: Microsoft Cloud Adoption Framework (CAF)
STANDARD_TAGS = {
    'Environment':        ['Production', 'Staging', 'Development', 'QA', 'DR'],
    'Owner':              [],                        # free-text email/team
    'CostCenter':         [],                        # billing code e.g. CC-1042
    'Project':            [],                        # application/workload name
    'Department':         ['Engineering', 'Finance', 'Operations', 'HR'],
    'Criticality':        ['High', 'Medium', 'Low'],
    'DataClassification': ['Confidential', 'Internal', 'Public', 'Restricted'],
    'MaintenanceWindow':  ['Sat 02:00-04:00 UTC', 'Sun 02:00-04:00 UTC'],
}


def _get_compute_client():
    # Build Azure Compute SDK client using service principal
    credential = ClientSecretCredential(
        tenant_id     = Config.TENANT_ID,
        client_id     = Config.CLIENT_ID,
        client_secret = Config.CLIENT_SECRET
    )
    return ComputeManagementClient(credential, Config.SUBSCRIPTION_ID)


def get_vm_tags(resource_group, vm_name):
    """Returns current tags dict for a single VM"""
    client = _get_compute_client()
    vm     = client.virtual_machines.get(resource_group, vm_name)
    return vm.tags or {}


def update_vm_tag(resource_group, vm_name, tag_key, tag_value):
    """
    Adds or updates a single tag on the VM.
    Existing tags are preserved — only the specified key changes.
    """
    client   = _get_compute_client()
    vm       = client.virtual_machines.get(resource_group, vm_name)
    tags     = vm.tags or {}

    # Upsert the single tag key
    tags[tag_key] = tag_value
    vm.tags       = tags

    # Apply the tag change via full VM update (Azure SDK requirement)
    poller = client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm
    )
    poller.result()
    return f"Tag '{tag_key}={tag_value}' set on '{vm_name}'"


def bulk_update_vm_tags(resource_group, vm_name, tags_dict):
    """
    Merges multiple tags onto the VM in a single Azure API call.
    Existing tags not in tags_dict are preserved.
    tags_dict: {key: value, ...}
    """
    client = _get_compute_client()
    vm     = client.virtual_machines.get(resource_group, vm_name)
    tags   = vm.tags or {}

    tags.update(tags_dict)
    vm.tags = tags

    poller = client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm
    )
    poller.result()
    count = len(tags_dict)
    return f"{count} tag(s) applied to '{vm_name}'"


def delete_vm_tag(resource_group, vm_name, tag_key):
    """Removes a single tag key from the VM — other tags untouched"""
    client = _get_compute_client()
    vm     = client.virtual_machines.get(resource_group, vm_name)
    tags   = vm.tags or {}

    # Remove key if present
    if tag_key in tags:
        del tags[tag_key]

    vm.tags = tags
    poller  = client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm
    )
    poller.result()
    return f"Tag '{tag_key}' removed from '{vm_name}'"


def get_all_vms_with_tags():
    """
    Returns all VMs with their current tags.
    Used by the admin Tags overview page.
    Tags are included in list_all() response — no extra API calls.
    """
    client   = _get_compute_client()
    vm_list  = []

    for vm in client.virtual_machines.list_all():
        resource_group = vm.id.split('/')[4]
        vm_list.append({
            'name':           vm.name,
            'resource_group': resource_group,
            'location':       vm.location,
            'tags':           vm.tags or {},
            'tag_count':      len(vm.tags) if vm.tags else 0,
        })

    return vm_list
