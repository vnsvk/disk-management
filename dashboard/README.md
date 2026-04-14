# Azure Disk Dashboard

This dashboard lets you:

- select any subscription available in your Azure CLI login
- scan managed disks live from Azure
- review V1, V2, eligible-for-migration, and unattached disks
- export the current results as CSV

## Run

Make sure Azure CLI is installed and authenticated:

```powershell
az login --use-device-code
python .\dashboard\app.py
```

Then open:

```text
http://127.0.0.1:8765
```

## Export options

- `Export CSV` downloads the full inventory when nothing is selected
- `Export CSV` downloads only selected unattached disks when you use the checkboxes in the `Unattached Disks` table
- `Select All V1` selects all currently eligible V1 disks for migration
- `Backup Before Migration` adds snapshot creation before `Migrate Selected`
- `Backup Selected` creates snapshots for selected migration disks without migrating them
- `Migrate Selected` migrates selected eligible V1 disks to `PremiumV2_LRS`
- `Select All` selects every unattached disk in the current view
- `Clear` clears unattached disk selection
- `Open Selected` opens the selected unattached disks in Azure Portal
- `Delete Selected` deletes the selected unattached disks directly from the dashboard after confirmation

The exported CSV contains portal links when available.

`Migrate Selected` performs real Azure changes. Attached data disks are deallocated with their VM before conversion and started again afterward. OS disks are not supported.

If you intentionally want to use a separate Azure CLI profile, set `AZURE_CONFIG_DIR` yourself before `az login` and before starting the dashboard. Otherwise the app uses your normal Azure CLI login context.
