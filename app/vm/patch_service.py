# ═══════════════════════════════════════════════════════════════
# Patch Service — Module 14
# AUM-ready with graceful fallbacks throughout.
#
# Design principle:
#   • When AUM / AutomaticByPlatform is NOT configured → return
#     'aum_not_enabled' state with zero code changes needed later.
#   • When AUM is configured the same functions return real data.
#   • All mutating operations go through the approval workflow.
#
# Portal scope (Phase 1):
#   View patch config, patch mode, ring from tags,
#   trigger assessment, trigger install via approval workflow,
#   configure patch mode, compliance dashboard.
# ═══════════════════════════════════════════════════════════════

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import (
    VirtualMachineInstallPatchesParameters,
    WindowsParameters,
    LinuxParameters,
)
from config import Config


# ── Patch classification options ────────────────────────────────
# Used in both UI dropdowns and API calls

WINDOWS_CLASSIFICATIONS = [
    'Critical',
    'Security',
    'UpdateRollup',
    'FeaturePack',
    'ServicePack',
    'Definition',
    'Tools',
    'Updates',
]

LINUX_CLASSIFICATIONS = [
    'Critical',
    'Security',
    'Other',
]

REBOOT_OPTIONS = [
    {'value': 'IfRequired',   'label': 'Reboot if required'},
    {'value': 'NeverReboot',  'label': 'Never reboot'},
    {'value': 'AlwaysReboot', 'label': 'Always reboot'},
]

# Standard patch ring tag key (matches AUM deployment rings)
RING_TAG_KEY = 'PatchRing'


# ── Client factory ───────────────────────────────────────────────

def _get_compute_client():
    cred = ClientSecretCredential(
        tenant_id     = Config.TENANT_ID,
        client_id     = Config.CLIENT_ID,
        client_secret = Config.CLIENT_SECRET
    )
    return ComputeManagementClient(cred, Config.SUBSCRIPTION_ID)


# ── Helper: read patch settings from a VM object ─────────────────

def _extract_patch_settings(vm):
    """
    Reads patch_mode, assessment_mode, and os_type from a
    VirtualMachine SDK object.
    Returns safe defaults when settings are absent (older VMs).
    """
    os_raw   = str(vm.storage_profile.os_disk.os_type).lower()
    os_type  = 'Linux' if 'linux' in os_raw else 'Windows'

    patch_mode      = 'Unknown'
    assessment_mode = 'Unknown'

    try:
        if os_type == 'Windows':
            ps = (
                vm.os_profile.windows_configuration.patch_settings
                if vm.os_profile and
                   vm.os_profile.windows_configuration
                else None
            )
            if ps:
                patch_mode = str(ps.patch_mode or 'Unknown')
                assessment_mode = str(
                    ps.assessment_mode or 'Unknown'
                )
        else:
            lc = (
                vm.os_profile.linux_configuration
                if vm.os_profile else None
            )
            if lc and hasattr(lc, 'patch_settings') \
                    and lc.patch_settings:
                patch_mode = str(
                    lc.patch_settings.patch_mode or 'Unknown'
                )
                assessment_mode = str(
                    getattr(
                        lc.patch_settings,
                        'assessment_mode',
                        'Unknown'
                    ) or 'Unknown'
                )
    except Exception:
        pass

    return os_type, patch_mode, assessment_mode


# ── Read current patch config for a single VM ────────────────────

def get_vm_patch_status(resource_group, vm_name):
    """
    Returns the patch configuration and readiness for a VM.

    AUM-ready: When patch_mode is 'AutomaticByPlatform' the portal
    shows real data. When it is anything else it shows
    'aum_not_enabled' with a one-click path to enable it.

    Return shape:
    {
        'vm_name':          str,
        'os_type':          'Linux' | 'Windows',
        'patch_mode':       str,        # AutomaticByPlatform | ImageDefault | …
        'assessment_mode':  str,        # AutomaticByPlatform | ImageDefault | …
        'aum_enabled':      bool,       # True iff patch_mode == AutomaticByPlatform
        'auto_assess':      bool,       # True iff assessment_mode == AutomaticByPlatform
        'ring':             str | None, # from PatchRing tag
        'ring_all':         list,       # all ring tags across VMs (populated by bulk)
        'status':           str,        # 'aum_not_enabled' | 'aum_ready' | 'error'
        'status_label':     str,        # human label
        'windows_classifications': list,  # for UI
        'linux_classifications':   list,
        'reboot_options':   list,
        'error':            str | None,
    }
    """
    try:
        client = _get_compute_client()
        vm     = client.virtual_machines.get(
                     resource_group, vm_name
                 )

        os_type, patch_mode, assessment_mode = \
            _extract_patch_settings(vm)

        # Ring from tag
        tags = vm.tags or {}
        ring = tags.get(RING_TAG_KEY) or tags.get('patchring') \
               or tags.get('UpdateRing') or None

        aum_enabled = (patch_mode == 'AutomaticByPlatform')
        auto_assess = (assessment_mode == 'AutomaticByPlatform')

        if aum_enabled:
            status       = 'aum_ready'
            status_label = 'AUM Enabled'
        else:
            status       = 'aum_not_enabled'
            status_label = 'AUM Not Enabled'

        print(f"─── PATCH STATUS ─────────────────────────")
        print(f"VM:         {vm_name}")
        print(f"OS:         {os_type}")
        print(f"PatchMode:  {patch_mode}")
        print(f"AssessMode: {assessment_mode}")
        print(f"AUM:        {aum_enabled}")
        print(f"Ring:       {ring}")
        print(f"──────────────────────────────────────────")

        return {
            'vm_name':        vm_name,
            'os_type':        os_type,
            'patch_mode':     patch_mode,
            'assessment_mode': assessment_mode,
            'aum_enabled':    aum_enabled,
            'auto_assess':    auto_assess,
            'ring':           ring,
            'status':         status,
            'status_label':   status_label,
            'windows_classifications': WINDOWS_CLASSIFICATIONS,
            'linux_classifications':   LINUX_CLASSIFICATIONS,
            'reboot_options': REBOOT_OPTIONS,
            'error':          None,
        }

    except Exception as e:
        print(f"[WARN] Patch status fetch failed for {vm_name}: {e}")
        return {
            'vm_name':        vm_name,
            'os_type':        'Unknown',
            'patch_mode':     'Unknown',
            'assessment_mode': 'Unknown',
            'aum_enabled':    False,
            'auto_assess':    False,
            'ring':           None,
            'status':         'error',
            'status_label':   'Error fetching patch config',
            'windows_classifications': WINDOWS_CLASSIFICATIONS,
            'linux_classifications':   LINUX_CLASSIFICATIONS,
            'reboot_options': REBOOT_OPTIONS,
            'error':          str(e),
        }


# ── Trigger patch assessment ─────────────────────────────────────

def trigger_patch_assessment(resource_group, vm_name):
    """
    Triggers Azure Guest Assessment via begin_assess_patches.
    Works for VMs in Running state.
    Assessment takes ~5-10 mins; results appear in Azure Portal.
    Returns a message string.
    """
    client = _get_compute_client()

    print(f"─── PATCH ASSESSMENT ─────────────────────")
    print(f"VM: {vm_name} / RG: {resource_group}")
    print(f"──────────────────────────────────────────")

    poller = client.virtual_machines.begin_assess_patches(
        resource_group, vm_name
    )
    result = poller.result()

    status_str = str(getattr(result, 'status', 'Completed'))
    msg = (
        f"Patch assessment triggered on '{vm_name}'. "
        f"Status: {status_str}. "
        f"Results will appear in Azure Portal within ~10 minutes."
    )
    print(f"[OK] {msg}")
    return msg


# ── Install patches ──────────────────────────────────────────────

def install_patches(resource_group, vm_name, os_type,
                    classifications, reboot_setting):
    """
    Installs patches via Azure begin_install_patches.
    VM must be Running.

    classifications: list of strings e.g. ['Critical', 'Security']
    reboot_setting:  'IfRequired' | 'NeverReboot' | 'AlwaysReboot'
    """
    client = _get_compute_client()

    print(f"─── PATCH INSTALL ────────────────────────")
    print(f"VM:              {vm_name}")
    print(f"OS:              {os_type}")
    print(f"Classifications: {classifications}")
    print(f"Reboot:          {reboot_setting}")
    print(f"──────────────────────────────────────────")

    if os_type == 'Windows':
        params = VirtualMachineInstallPatchesParameters(
            maximum_duration = 'PT2H',
            reboot_setting   = reboot_setting,
            windows_parameters = WindowsParameters(
                classifications_to_include = classifications
            )
        )
    else:
        params = VirtualMachineInstallPatchesParameters(
            maximum_duration = 'PT2H',
            reboot_setting   = reboot_setting,
            linux_parameters = LinuxParameters(
                classifications_to_include = classifications
            )
        )

    poller = client.virtual_machines.begin_install_patches(
        resource_group, vm_name, params
    )
    result = poller.result()

    installed = getattr(result, 'installed_patch_count', '?')
    failed    = getattr(result, 'failed_patch_count',    0)
    status    = str(getattr(result, 'status', 'Completed'))

    msg = (
        f"Patch install on '{vm_name}' {status}. "
        f"Installed: {installed} patches"
        + (f", Failed: {failed}" if failed else "")
        + f". Reboot setting: {reboot_setting}."
    )
    print(f"[OK] {msg}")
    return msg


# ── Set patch mode ───────────────────────────────────────────────

def set_patch_mode(resource_group, vm_name, os_type, patch_mode):
    """
    Configures the VM's patch mode via Azure Compute API.
    patch_mode for Linux/Windows: 'AutomaticByPlatform'
    patch_mode for Windows only:  'AutomaticByOS' | 'Manual'
    patch_mode for Linux only:    'ImageDefault'

    Sets assessment_mode to AutomaticByPlatform when enabling AUM.
    Does NOT require VM restart.
    """
    client = _get_compute_client()

    print(f"─── SET PATCH MODE ───────────────────────")
    print(f"VM:         {vm_name}")
    print(f"OS:         {os_type}")
    print(f"PatchMode:  {patch_mode}")
    print(f"──────────────────────────────────────────")

    vm = client.virtual_machines.get(resource_group, vm_name)

    if os_type == 'Windows':
        if not vm.os_profile.windows_configuration:
            raise ValueError("No Windows configuration found")
        if not vm.os_profile.windows_configuration.patch_settings:
            from azure.mgmt.compute.models import PatchSettings
            vm.os_profile.windows_configuration.patch_settings = \
                PatchSettings()
        vm.os_profile.windows_configuration.patch_settings\
            .patch_mode = patch_mode
        if patch_mode == 'AutomaticByPlatform':
            vm.os_profile.windows_configuration.patch_settings\
                .assessment_mode = 'AutomaticByPlatform'
    else:
        if not vm.os_profile.linux_configuration:
            raise ValueError("No Linux configuration found")
        if not hasattr(
            vm.os_profile.linux_configuration, 'patch_settings'
        ) or not vm.os_profile.linux_configuration.patch_settings:
            from azure.mgmt.compute.models import (
                LinuxPatchSettings
            )
            vm.os_profile.linux_configuration.patch_settings = \
                LinuxPatchSettings()
        vm.os_profile.linux_configuration\
            .patch_settings.patch_mode = patch_mode
        if patch_mode == 'AutomaticByPlatform':
            vm.os_profile.linux_configuration\
                .patch_settings.assessment_mode = \
                'AutomaticByPlatform'

    poller = client.virtual_machines.begin_create_or_update(
        resource_group, vm_name, vm
    )
    poller.result()

    msg = (
        f"Patch mode on '{vm_name}' set to '{patch_mode}'"
        + (" with AutomaticByPlatform assessment."
           if patch_mode == 'AutomaticByPlatform' else ".")
    )
    print(f"[OK] {msg}")
    return msg


# ── Compliance summary for all VMs ───────────────────────────────

def get_all_vms_patch_summary():
    """
    Returns patch config summary for all VMs in subscription.
    Used by the compliance dashboard.

    Returns list of dicts:
    [
        {
            'vm_name':      str,
            'resource_group': str,
            'os_type':      str,
            'power_state':  str,
            'patch_mode':   str,
            'aum_enabled':  bool,
            'auto_assess':  bool,
            'ring':         str | None,
        },
        ...
    ]
    """
    client = _get_compute_client()
    result = []

    try:
        vms = client.virtual_machines.list_all()
        for vm in vms:
            try:
                parts = vm.id.split('/')
                rg    = parts[4] if len(parts) > 4 else 'unknown'

                os_type, patch_mode, assessment_mode = \
                    _extract_patch_settings(vm)

                tags = vm.tags or {}
                ring = (
                    tags.get(RING_TAG_KEY)
                    or tags.get('patchring')
                    or tags.get('UpdateRing')
                    or None
                )

                # Power state from instance view if available
                # (list_all doesn't include instance view)
                power_state = 'Unknown'
                if vm.instance_view and vm.instance_view.statuses:
                    for s in vm.instance_view.statuses:
                        if s.code and 'PowerState' in s.code:
                            power_state = s.code.replace(
                                'PowerState/', ''
                            ).capitalize()
                            break

                sub_id = (
                    parts[2] if len(parts) > 2
                    else Config.SUBSCRIPTION_ID
                )

                result.append({
                    'vm_name':         vm.name,
                    'resource_group':  rg,
                    'subscription_id': sub_id,
                    'os_type':         os_type,
                    'power_state':     power_state,
                    'patch_mode':      patch_mode,
                    'aum_enabled':     (
                        patch_mode == 'AutomaticByPlatform'
                    ),
                    'auto_assess':     (
                        assessment_mode == 'AutomaticByPlatform'
                    ),
                    'ring':            ring,
                })

            except Exception as e:
                print(f"[WARN] Skipping VM {vm.name}: {e}")

    except Exception as e:
        print(f"[ERR] Patch summary fetch failed: {e}")
        raise

    return result


def get_patch_classifications(os_type):
    """Returns classification list for the given OS type"""
    if os_type == 'Windows':
        return WINDOWS_CLASSIFICATIONS
    return LINUX_CLASSIFICATIONS
