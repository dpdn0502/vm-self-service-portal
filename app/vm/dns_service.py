# ═══════════════════════════════════════════════════════════════
# DNS Service — Module 14, Phase 1
# VM-scoped DNS operations:
#   • View current DNS config (hostname, NIC DNS servers, FQDN)
#   • Change VM hostname via Run Command
#   • Update NIC custom DNS servers via Azure Network API
#   • Update DNS search suffixes via Run Command
# ═══════════════════════════════════════════════════════════════

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from config import Config


# ── Client factory ─────────────────────────────────────────────

def _get_clients():
    cred = ClientSecretCredential(
        tenant_id     = Config.TENANT_ID,
        client_id     = Config.CLIENT_ID,
        client_secret = Config.CLIENT_SECRET
    )
    return (
        ComputeManagementClient(cred, Config.SUBSCRIPTION_ID),
        NetworkManagementClient(cred, Config.SUBSCRIPTION_ID)
    )


# ── Read current DNS config ────────────────────────────────────

def get_vm_dns_config(resource_group, vm_name):
    """
    Returns the current DNS configuration for a VM.
    {
        'os_type':  'Linux' | 'Windows',
        'hostname': str,
        'nics': [
            {
                'nic_name':               str,
                'nic_rg':                 str,
                'dns_servers':            [ip, ...],   # custom (empty = Azure default)
                'applied_dns_servers':    [ip, ...],   # effective (includes VNET default)
                'internal_dns_name_label': str,
                'internal_fqdn':          str,
            }, ...
        ]
    }
    """
    compute_client, net_client = _get_clients()
    vm = compute_client.virtual_machines.get(resource_group, vm_name)

    os_raw  = str(vm.storage_profile.os_disk.os_type).lower()
    os_type = 'Linux' if 'linux' in os_raw else 'Windows'
    hostname = (
        vm.os_profile.computer_name
        if vm.os_profile and vm.os_profile.computer_name
        else vm_name
    )

    nics = []
    if vm.network_profile and vm.network_profile.network_interfaces:
        for nic_ref in vm.network_profile.network_interfaces:
            parts    = nic_ref.id.split('/')
            nic_rg   = parts[4]
            nic_name = parts[-1]

            try:
                nic = net_client.network_interfaces.get(nic_rg, nic_name)
                dns = nic.dns_settings
                nics.append({
                    'nic_name':               nic_name,
                    'nic_rg':                 nic_rg,
                    'dns_servers':            list(dns.dns_servers or []),
                    'applied_dns_servers':    list(dns.applied_dns_servers or []),
                    'internal_dns_name_label': dns.internal_dns_name_label or '',
                    'internal_fqdn':          dns.internal_fqdn or '',
                })
            except Exception as e:
                print(f"⚠️ Could not fetch NIC {nic_name}: {e}")
                nics.append({
                    'nic_name':               nic_name,
                    'nic_rg':                 nic_rg,
                    'dns_servers':            [],
                    'applied_dns_servers':    [],
                    'internal_dns_name_label': '',
                    'internal_fqdn':          '',
                })

    print(f"─── DNS CONFIG ──────────────────────────")
    print(f"VM:       {vm_name}")
    print(f"Hostname: {hostname}")
    print(f"NICs:     {len(nics)}")
    print(f"────────────────────────────────────────")

    return {
        'os_type':  os_type,
        'hostname': hostname,
        'nics':     nics,
    }


# ── NIC DNS server update ──────────────────────────────────────

def update_nic_dns_servers(nic_rg, nic_name, dns_servers):
    """
    Sets custom DNS servers on a NIC via Azure Network API.
    dns_servers: list of IP strings
                 (empty list resets to Azure / VNET default)
    No VM restart required — takes effect within ~30 seconds.
    """
    _, net_client = _get_clients()

    nic = net_client.network_interfaces.get(nic_rg, nic_name)
    nic.dns_settings.dns_servers = dns_servers

    poller = net_client.network_interfaces.begin_create_or_update(
        nic_rg, nic_name, nic
    )
    poller.result()

    if dns_servers:
        servers_str = ', '.join(dns_servers)
        msg = f"DNS servers on NIC '{nic_name}' set to: {servers_str}"
    else:
        msg = f"DNS servers on NIC '{nic_name}' reset to Azure / VNET default"

    print(f"✅ {msg}")
    return msg


# ── Hostname change via Run Command ────────────────────────────

def change_vm_hostname(resource_group, vm_name, os_type, new_hostname):
    """
    Changes the OS hostname via Azure Run Command.
    VM must be in Running state.
    Windows: triggers an automatic restart for the change to take effect.
    """
    compute_client, _ = _get_clients()

    print(f"─── HOSTNAME CHANGE ─────────────────────")
    print(f"VM:       {vm_name}")
    print(f"OS:       {os_type}")
    print(f"Hostname: {new_hostname}")
    print(f"────────────────────────────────────────")

    if os_type == 'Linux':
        run_input = {
            'command_id': 'RunShellScript',
            'script': [
                f'hostnamectl set-hostname {new_hostname}',
                f'echo "{new_hostname}" > /etc/hostname',
            ]
        }
    else:
        # Windows requires a restart for hostname changes
        run_input = {
            'command_id': 'RunPowerShellScript',
            'script': [
                f'Rename-Computer -NewName "{new_hostname}" '
                f'-Force -Restart'
            ]
        }

    poller = compute_client.virtual_machines.begin_run_command(
        resource_group, vm_name, run_input
    )
    poller.result()

    note = ' (Windows will restart to apply change)' \
           if os_type == 'Windows' else ''
    msg = f"Hostname changed to '{new_hostname}' on '{vm_name}'{note}"
    print(f"✅ {msg}")
    return msg


# ── DNS search suffix update via Run Command ───────────────────

def update_dns_search_suffix(resource_group, vm_name,
                              os_type, suffixes):
    """
    Updates DNS search suffixes via Azure Run Command.
    suffixes: list of domain names (e.g. ['corp.local', 'dev.corp.local'])
    VM must be in Running state.
    Linux: updates /etc/resolv.conf search line.
    Windows: uses Set-DnsClientGlobalSetting.
    """
    compute_client, _ = _get_clients()

    print(f"─── DNS SUFFIX UPDATE ───────────────────")
    print(f"VM:       {vm_name}")
    print(f"OS:       {os_type}")
    print(f"Suffixes: {suffixes}")
    print(f"────────────────────────────────────────")

    if os_type == 'Linux':
        suffix_str = ' '.join(suffixes)
        run_input = {
            'command_id': 'RunShellScript',
            'script': [
                'sed -i "/^search /d" /etc/resolv.conf',
                f'echo "search {suffix_str}" >> /etc/resolv.conf',
            ]
        }
    else:
        ps_list = ', '.join([f'"{s}"' for s in suffixes])
        run_input = {
            'command_id': 'RunPowerShellScript',
            'script': [
                f'Set-DnsClientGlobalSetting '
                f'-SuffixSearchList @({ps_list})'
            ]
        }

    poller = compute_client.virtual_machines.begin_run_command(
        resource_group, vm_name, run_input
    )
    poller.result()

    msg = (
        f"DNS search suffixes on '{vm_name}' "
        f"set to: {', '.join(suffixes)}"
    )
    print(f"✅ {msg}")
    return msg
