[CmdletBinding(DefaultParameterSetName = 'Default', SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Inventory', 'Migrate', 'Cleanup', 'All')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [string]$ResourceGroupName,

    [string]$ReportPath,

    [switch]$Apply,

    [switch]$DisableHostCaching,

    [switch]$CreateSnapshotBefore
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:PremiumV2Regions = $null

function Write-Section {
    param([string]$Message)

    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Assert-AzureCli {
    $null = Get-Command az -ErrorAction Stop
    $account = az account show --subscription $SubscriptionId --only-show-errors -o json 2>$null

    if (-not $account) {
        throw "Azure CLI is not authenticated for subscription '$SubscriptionId'. Run 'az login' and retry."
    }
}

function Invoke-AzJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & az @Arguments --subscription $SubscriptionId --only-show-errors -o json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed: az $($Arguments -join ' ')"
    }

    if ([string]::IsNullOrWhiteSpace($output)) {
        return $null
    }

    return $output | ConvertFrom-Json -Depth 100
}

function Invoke-AzVoid {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & az @Arguments --subscription $SubscriptionId --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed: az $($Arguments -join ' ')"
    }
}

function Get-ManagedDisks {
    $arguments = @('disk', 'list')

    if ($ResourceGroupName) {
        $arguments += @('--resource-group', $ResourceGroupName)
    }

    return @(Invoke-AzJson -Arguments $arguments)
}

function Get-VirtualMachines {
    $arguments = @('vm', 'list')

    if ($ResourceGroupName) {
        $arguments += @('--resource-group', $ResourceGroupName)
    }

    return @(Invoke-AzJson -Arguments $arguments)
}

function Get-DiskSkuClass {
    param([string]$SkuName)

    switch ($SkuName) {
        'Premium_LRS' { return 'V1' }
        'PremiumV2_LRS' { return 'V2' }
        default { return 'Other' }
    }
}

function Test-PremiumV2AvailableInRegion {
    param([string]$Region)

    if ([string]::IsNullOrWhiteSpace($Region)) {
        return $false
    }

    try {
        if ($null -eq $script:PremiumV2Regions) {
            $regions = & az vm list-skus --resource-type disks --query "[?name=='PremiumV2_LRS'].locationInfo[].location" --subscription $SubscriptionId --only-show-errors -o tsv
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to query disk SKUs"
            }

            $script:PremiumV2Regions = @($regions)
        }

        return $script:PremiumV2Regions -contains $Region
    }
    catch {
        Write-Warning "Could not verify Premium SSD v2 availability in region '$Region'. Azure will enforce the final validation during conversion."
        return $true
    }
}

function Build-VmDiskMap {
    param([object[]]$VirtualMachines)

    $map = @{}

    foreach ($vm in $VirtualMachines) {
        if ($vm.storageProfile.osDisk.managedDisk.id) {
            $map[$vm.storageProfile.osDisk.managedDisk.id.ToLowerInvariant()] = [pscustomobject]@{
                VmId        = $vm.id
                VmName      = $vm.name
                ResourceGroup = $vm.resourceGroup
                IsOsDisk    = $true
                Lun         = $null
                Caching     = $vm.storageProfile.osDisk.caching
            }
        }

        foreach ($dataDisk in @($vm.storageProfile.dataDisks)) {
            if ($dataDisk.managedDisk.id) {
                $map[$dataDisk.managedDisk.id.ToLowerInvariant()] = [pscustomobject]@{
                    VmId          = $vm.id
                    VmName        = $vm.name
                    ResourceGroup = $vm.resourceGroup
                    IsOsDisk      = $false
                    Lun           = $dataDisk.lun
                    Caching       = $dataDisk.caching
                }
            }
        }
    }

    return $map
}

function Get-Inventory {
    $disks = Get-ManagedDisks
    $vms = Get-VirtualMachines
    $vmDiskMap = Build-VmDiskMap -VirtualMachines $vms

    $inventory = foreach ($disk in $disks) {
        $diskId = $disk.id.ToLowerInvariant()
        $attachment = $null

        if ($vmDiskMap.ContainsKey($diskId)) {
            $attachment = $vmDiskMap[$diskId]
        }

        $isAttached = -not [string]::IsNullOrWhiteSpace($disk.managedBy) -or $null -ne $attachment -or $disk.diskState -eq 'Attached'

        [pscustomobject]@{
            SubscriptionId   = $SubscriptionId
            ResourceGroup    = $disk.resourceGroup
            DiskName         = $disk.name
            Location         = $disk.location
            Sku              = $disk.sku.name
            DiskVersion      = Get-DiskSkuClass -SkuName $disk.sku.name
            DiskState        = $disk.diskState
            ManagedBy        = $disk.managedBy
            Attached         = $isAttached
            VmName           = if ($attachment) { $attachment.VmName } else { $null }
            IsOsDisk         = if ($attachment) { $attachment.IsOsDisk } else { $false }
            Lun              = if ($attachment) { $attachment.Lun } else { $null }
            Caching          = if ($attachment) { $attachment.Caching } else { $null }
            LogicalSectorSize = $disk.logicalSectorSize
            BurstingEnabled  = $disk.burstingEnabled
            Zones            = if ($disk.zones) { ($disk.zones -join ',') } else { $null }
            Id               = $disk.id
            Unattached       = -not $isAttached
        }
    }

    return @($inventory)
}

function Export-Inventory {
    param([object[]]$Inventory)

    if (-not $ReportPath) {
        return
    }

    $parent = Split-Path -Path $ReportPath -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }

    $extension = [System.IO.Path]::GetExtension($ReportPath)
    if ($extension.ToLowerInvariant() -ne '.json') {
        throw "Unsupported report format '$extension'. Use a .json file."
    }

    $Inventory | ConvertTo-Json -Depth 10 | Set-Content -Path $ReportPath
    Write-Host "Report written to $ReportPath" -ForegroundColor Green
}

function Show-InventorySummary {
    param([object[]]$Inventory)

    Write-Section "Inventory Summary"

    $summary = [pscustomobject]@{
        TotalDisks      = $Inventory.Count
        V1Disks         = @($Inventory | Where-Object { $_.DiskVersion -eq 'V1' }).Count
        V2Disks         = @($Inventory | Where-Object { $_.DiskVersion -eq 'V2' }).Count
        OtherDisks      = @($Inventory | Where-Object { $_.DiskVersion -eq 'Other' }).Count
        UnattachedDisks = @($Inventory | Where-Object { $_.Unattached }).Count
    }

    $summary | Format-List | Out-Host

    Write-Section "Disk Details"
    $Inventory |
        Sort-Object ResourceGroup, DiskName |
        Select-Object ResourceGroup, DiskName, Sku, DiskVersion, Attached, VmName, IsOsDisk, LogicalSectorSize, BurstingEnabled, DiskState |
        Format-Table -AutoSize |
        Out-Host
}

function Show-MigrationSummary {
    param([object[]]$Plan)

    Write-Section "Eligible For Migration"
    $eligible = @($Plan | Where-Object { $_.Eligible })
    if ($eligible.Count -eq 0) {
        Write-Host "No disks are currently eligible for Premium SSD v2 migration." -ForegroundColor Yellow
    }
    else {
        $eligible |
            Select-Object ResourceGroup, VmName, DiskName, PlannedSku |
            Format-Table -AutoSize |
            Out-Host
    }

    Write-Section "Skipped Disks"
    $skipped = @($Plan | Where-Object { -not $_.Eligible })
    if ($skipped.Count -eq 0) {
        Write-Host "No disks were skipped." -ForegroundColor Green
    }
    else {
        $skipped |
            Select-Object ResourceGroup, VmName, DiskName, Reasons |
            Format-Table -Wrap -AutoSize |
            Out-Host
    }
}

function Get-MigrationPlan {
    param([object[]]$Inventory)

    $plan = foreach ($disk in $Inventory) {
        $reasons = [System.Collections.Generic.List[string]]::new()
        $eligible = $true

        if ($disk.DiskVersion -ne 'V1') {
            $eligible = $false
            if ($disk.DiskVersion -eq 'V2') {
                $reasons.Add('Disk is already Premium SSD v2')
            }
            else {
                $reasons.Add("SKU '$($disk.Sku)' is not Premium_LRS")
            }
        }

        if (-not (Test-PremiumV2AvailableInRegion -Region $disk.Location)) {
            $eligible = $false
            $reasons.Add("Region '$($disk.Location)' does not report Premium SSD v2 availability")
        }

        if (-not $disk.Attached) {
            $eligible = $false
            $reasons.Add('Disk is unattached')
        }

        if ($disk.IsOsDisk -eq $true) {
            $eligible = $false
            $reasons.Add('OS disks cannot be converted to Premium SSD v2')
        }

        if ($null -eq $disk.LogicalSectorSize -or [int]$disk.LogicalSectorSize -ne 512) {
            $eligible = $false
            $reasons.Add("Logical sector size '$($disk.LogicalSectorSize)' is not supported for direct conversion")
        }

        if ($disk.BurstingEnabled -eq $true) {
            $eligible = $false
            $reasons.Add('Bursting is enabled')
        }

        if ($disk.Caching -and $disk.Caching -ne 'None') {
            if ($DisableHostCaching) {
                $reasons.Add("Host caching '$($disk.Caching)' will be changed to 'None'")
            }
            else {
                $eligible = $false
                $reasons.Add("Host caching '$($disk.Caching)' must be disabled first")
            }
        }

        [pscustomobject]@{
            DiskName       = $disk.DiskName
            ResourceGroup  = $disk.ResourceGroup
            VmName         = $disk.VmName
            Eligible       = $eligible
            PlannedSku     = if ($eligible) { 'PremiumV2_LRS' } else { $null }
            Reasons        = if ($reasons.Count -gt 0) { $reasons -join '; ' } else { 'Ready for conversion' }
            Caching        = $disk.Caching
            Lun            = $disk.Lun
            Id             = $disk.Id
        }
    }

    return @($plan)
}

function New-DiskSnapshot {
    param(
        [string]$ResourceGroup,
        [string]$DiskName,
        [string]$Location,
        [string]$SourceDiskId,
        [string]$Sku
    )

    $timestamp = Get-Date -Format 'yyyyMMddHHmmss'
    $safeDiskName = if ($DiskName.Length -gt 60) { $DiskName.Substring(0, 60) } else { $DiskName }
    $snapshotName = "snapshot-$safeDiskName-$timestamp"

    Write-Host "Creating snapshot '$snapshotName' for disk '$DiskName'"

    Invoke-AzVoid -Arguments @(
        'snapshot', 'create',
        '--resource-group', $ResourceGroup,
        '--name', $snapshotName,
        '--location', $Location,
        '--source', $SourceDiskId,
        '--sku', $Sku
    )
}

function Update-DiskCachingToNone {
    param([pscustomobject]$MigrationItem)

    if ($MigrationItem.Caching -eq 'None' -or $null -eq $MigrationItem.Lun) {
        return
    }

    Write-Host "Setting host caching to None for disk '$($MigrationItem.DiskName)' on VM '$($MigrationItem.VmName)' (LUN $($MigrationItem.Lun))"
    Invoke-AzVoid -Arguments @(
        'vm', 'update',
        '--resource-group', $MigrationItem.ResourceGroup,
        '--name', $MigrationItem.VmName,
        '--disk-caching', "$($MigrationItem.Lun)=None"
    )
}

function Invoke-Migration {
    param([object[]]$Inventory)

    $plan = Get-MigrationPlan -Inventory $Inventory

    Show-MigrationSummary -Plan $plan

    $eligibleDisks = @($plan | Where-Object { $_.Eligible })
    if ($eligibleDisks.Count -eq 0) {
        Write-Host "No eligible disks found for Premium SSD v2 conversion." -ForegroundColor Yellow
        return
    }

    if (-not $Apply) {
        Write-Host "Dry run complete. Re-run with -Apply to execute conversions." -ForegroundColor Yellow
        return
    }

    $affectedVmGroups = $eligibleDisks | Group-Object ResourceGroup, VmName
    $deallocatedVmTargets = [System.Collections.Generic.List[object]]::new()

    try {
        foreach ($group in $affectedVmGroups) {
            $sample = $group.Group[0]
            if ($PSCmdlet.ShouldProcess("$($sample.ResourceGroup)/$($sample.VmName)", 'Deallocate VM for disk conversion')) {
                Write-Host "Deallocating VM '$($sample.VmName)' in resource group '$($sample.ResourceGroup)'"
                Invoke-AzVoid -Arguments @(
                    'vm', 'deallocate',
                    '--resource-group', $sample.ResourceGroup,
                    '--name', $sample.VmName
                )
                $deallocatedVmTargets.Add($sample)
            }
        }

        foreach ($disk in $eligibleDisks) {
            if ($DisableHostCaching -and $disk.Caching -and $disk.Caching -ne 'None') {
                Update-DiskCachingToNone -MigrationItem $disk
            }

            if ($CreateSnapshotBefore -and $PSCmdlet.ShouldProcess("$($disk.ResourceGroup)/$($disk.DiskName)", 'Create snapshot before conversion')) {
                $inventoryItem = $Inventory | Where-Object { $_.Id -eq $disk.Id } | Select-Object -First 1
                if ($null -eq $inventoryItem) {
                    throw "Unable to locate inventory data for disk '$($disk.DiskName)' before snapshot creation."
                }

                New-DiskSnapshot -ResourceGroup $disk.ResourceGroup -DiskName $disk.DiskName -Location $inventoryItem.Location -SourceDiskId $disk.Id -Sku $inventoryItem.Sku
            }

            if ($PSCmdlet.ShouldProcess("$($disk.ResourceGroup)/$($disk.DiskName)", 'Convert disk to PremiumV2_LRS')) {
                Write-Host "Converting disk '$($disk.DiskName)' to PremiumV2_LRS"
                Invoke-AzVoid -Arguments @(
                    'disk', 'update',
                    '--resource-group', $disk.ResourceGroup,
                    '--name', $disk.DiskName,
                    '--sku', 'PremiumV2_LRS'
                )
            }
        }
    }
    finally {
        foreach ($vm in $deallocatedVmTargets) {
            if ($PSCmdlet.ShouldProcess("$($vm.ResourceGroup)/$($vm.VmName)", 'Start VM after disk conversion')) {
                Write-Host "Starting VM '$($vm.VmName)' in resource group '$($vm.ResourceGroup)'"
                Invoke-AzVoid -Arguments @(
                    'vm', 'start',
                    '--resource-group', $vm.ResourceGroup,
                    '--name', $vm.VmName
                )
            }
        }
    }
}

function Invoke-Cleanup {
    param([object[]]$Inventory)

    $unattached = @($Inventory | Where-Object { $_.Unattached })

    Write-Section "Unattached Disks"
    if ($unattached.Count -eq 0) {
        Write-Host "No unattached disks found." -ForegroundColor Green
        return
    }

    $unattached |
        Select-Object ResourceGroup, DiskName, Sku, DiskState, Location |
        Format-Table -AutoSize |
        Out-Host

    if (-not $Apply) {
        Write-Host "Dry run complete. Re-run with -Apply to delete these disks." -ForegroundColor Yellow
        return
    }

    foreach ($disk in $unattached) {
        if ($PSCmdlet.ShouldProcess("$($disk.ResourceGroup)/$($disk.DiskName)", 'Delete unattached managed disk')) {
            $currentDisk = Invoke-AzJson -Arguments @(
                'disk', 'show',
                '--resource-group', $disk.ResourceGroup,
                '--name', $disk.DiskName
            )

            if (-not [string]::IsNullOrWhiteSpace($currentDisk.managedBy) -or $currentDisk.diskState -eq 'Attached') {
                throw "Disk '$($disk.DiskName)' is attached and cannot be deleted."
            }

            Write-Host "Deleting unattached disk '$($disk.DiskName)' from resource group '$($disk.ResourceGroup)'"
            Invoke-AzVoid -Arguments @(
                'disk', 'delete',
                '--resource-group', $disk.ResourceGroup,
                '--name', $disk.DiskName,
                '--yes'
            )
        }
    }
}

Assert-AzureCli
$inventory = Get-Inventory
Export-Inventory -Inventory $inventory

switch ($Action) {
    'Inventory' {
        Show-InventorySummary -Inventory $inventory
    }
    'Migrate' {
        Show-InventorySummary -Inventory $inventory
        Invoke-Migration -Inventory $inventory
    }
    'Cleanup' {
        Show-InventorySummary -Inventory $inventory
        Invoke-Cleanup -Inventory $inventory
    }
    'All' {
        Show-InventorySummary -Inventory $inventory
        Invoke-Migration -Inventory $inventory
        $postMigrationInventory = Get-Inventory
        Invoke-Cleanup -Inventory $postMigrationInventory
    }
}
