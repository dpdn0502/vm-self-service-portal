# ═══════════════════════════════════════════════════════════════
# Timezone Service — Module 13
# Sets VM OS timezone via Azure Run Command
# ═══════════════════════════════════════════════════════════════

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from config import Config


# ── Common timezone list ───────────────────────────────────────
# Each entry: human label, IANA name (Linux), Windows TZ ID

TIMEZONES = [
    {'label': 'UTC',
     'linux': 'UTC',
     'windows': 'UTC'},

    {'label': 'US Eastern (UTC-5/-4)',
     'linux': 'America/New_York',
     'windows': 'Eastern Standard Time'},

    {'label': 'US Central (UTC-6/-5)',
     'linux': 'America/Chicago',
     'windows': 'Central Standard Time'},

    {'label': 'US Mountain (UTC-7/-6)',
     'linux': 'America/Denver',
     'windows': 'Mountain Standard Time'},

    {'label': 'US Pacific (UTC-8/-7)',
     'linux': 'America/Los_Angeles',
     'windows': 'Pacific Standard Time'},

    {'label': 'US Alaska (UTC-9/-8)',
     'linux': 'America/Anchorage',
     'windows': 'Alaskan Standard Time'},

    {'label': 'US Hawaii (UTC-10)',
     'linux': 'Pacific/Honolulu',
     'windows': 'Hawaiian Standard Time'},

    {'label': 'Canada Atlantic (UTC-4/-3)',
     'linux': 'America/Halifax',
     'windows': 'Atlantic Standard Time'},

    {'label': 'Brazil / Brasilia (UTC-3)',
     'linux': 'America/Sao_Paulo',
     'windows': 'E. South America Standard Time'},

    {'label': 'UK / London (UTC+0/+1)',
     'linux': 'Europe/London',
     'windows': 'GMT Standard Time'},

    {'label': 'W. Europe / Amsterdam (UTC+1/+2)',
     'linux': 'Europe/Amsterdam',
     'windows': 'W. Europe Standard Time'},

    {'label': 'C. Europe / Berlin (UTC+1/+2)',
     'linux': 'Europe/Berlin',
     'windows': 'Central Europe Standard Time'},

    {'label': 'C. Europe / Paris (UTC+1/+2)',
     'linux': 'Europe/Paris',
     'windows': 'Romance Standard Time'},

    {'label': 'E. Europe / Helsinki (UTC+2/+3)',
     'linux': 'Europe/Helsinki',
     'windows': 'FLE Standard Time'},

    {'label': 'Moscow (UTC+3)',
     'linux': 'Europe/Moscow',
     'windows': 'Russian Standard Time'},

    {'label': 'Gulf / Dubai (UTC+4)',
     'linux': 'Asia/Dubai',
     'windows': 'Arabian Standard Time'},

    {'label': 'India (UTC+5:30)',
     'linux': 'Asia/Kolkata',
     'windows': 'India Standard Time'},

    {'label': 'Bangladesh (UTC+6)',
     'linux': 'Asia/Dhaka',
     'windows': 'Bangladesh Standard Time'},

    {'label': 'Indochina / Bangkok (UTC+7)',
     'linux': 'Asia/Bangkok',
     'windows': 'SE Asia Standard Time'},

    {'label': 'China / Singapore (UTC+8)',
     'linux': 'Asia/Singapore',
     'windows': 'Singapore Standard Time'},

    {'label': 'Japan / Korea (UTC+9)',
     'linux': 'Asia/Tokyo',
     'windows': 'Tokyo Standard Time'},

    {'label': 'Australia AEST (UTC+10)',
     'linux': 'Australia/Sydney',
     'windows': 'AUS Eastern Standard Time'},

    {'label': 'New Zealand (UTC+12/+13)',
     'linux': 'Pacific/Auckland',
     'windows': 'New Zealand Standard Time'},
]


def get_common_timezones():
    """Returns the list of common timezones for template rendering"""
    return TIMEZONES


def set_vm_timezone(resource_group, vm_name, os_type, timezone_id):
    """
    Sets the OS timezone on a running VM using Azure Run Command.

    os_type:     'Linux' or 'Windows'
    timezone_id: IANA name for Linux (e.g. 'America/New_York')
                 Windows TZ ID for Windows (e.g. 'Eastern Standard Time')

    Requires the VM to be in Running state.
    """
    credential = ClientSecretCredential(
        tenant_id     = Config.TENANT_ID,
        client_id     = Config.CLIENT_ID,
        client_secret = Config.CLIENT_SECRET
    )
    client = ComputeManagementClient(
        credential, Config.SUBSCRIPTION_ID
    )

    print(f"─── SET TIMEZONE ────────────────────────")
    print(f"VM:       {vm_name}")
    print(f"RG:       {resource_group}")
    print(f"OS:       {os_type}")
    print(f"Timezone: {timezone_id}")
    print(f"────────────────────────────────────────")

    if os_type == 'Linux':
        run_input = {
            'command_id': 'RunShellScript',
            'script': [
                f'timedatectl set-timezone {timezone_id}'
            ]
        }
    else:
        run_input = {
            'command_id': 'RunPowerShellScript',
            'script': [
                f'Set-TimeZone -Id "{timezone_id}"'
            ]
        }

    poller = client.virtual_machines.begin_run_command(
        resource_group, vm_name, run_input
    )
    poller.result()

    print(f"✅ Timezone set to '{timezone_id}' on '{vm_name}'")
    return f"Timezone set to '{timezone_id}' on '{vm_name}'"
