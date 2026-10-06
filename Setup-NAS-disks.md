# Setup NAS disks (Btrfs or mdadm RAID 1)

## What this document covers

This document covers setting up the two NVMe SSDs of the CM4-NVME-NAS-BOX as a mirror (RAID 1), with either Btrfs or mdadm + ext4.

Prerequisite: the CM4 is installed and running as described in [Setup CM4 + CM4-NVME-NAS-BOX](./Setup-CM4-NVME-NAS-BOX.md), with both SSDs added and recognized (step 7 of that guide).

## TOC

1. [Choose: Btrfs RAID 1 or mdadm RAID 1](#1-choose-btrfs-raid-1-or-mdadm-raid-1)
2. [Option A: Btrfs RAID 1](#2-option-a-btrfs-raid-1)
3. [Option B: mdadm RAID 1 + ext4](#3-option-b-mdadm-raid-1--ext4)
4. [Next steps](#4-next-steps)
5. [Sources](#5-sources)

## 1. Choose: Btrfs RAID 1 or mdadm RAID 1

With two 2 TB NVMe SSDs, the most suitable setup is a **mirror (RAID 1)**: each disk holds a complete copy of the data, and the NAS keeps working if one of the two fails. There are two ways to do it. **Choose only one**: never use both on the same disks.

| | Btrfs RAID 1 (native) | mdadm RAID 1 + ext4 |
|---|---|---|
| Principle | The filesystem manages the mirror across both disks itself | mdadm creates a virtual disk `/dev/md0`, then formatted as ext4 |
| Usable capacity (2 × 2 TB) | about 2 TB | about 2 TB |
| Silent corruption detection | Yes: checksum on every data block | Not for data (only ext4 metadata is protected) |
| Automatic repair | Yes: the healthy copy replaces the corrupted one | No: mdadm cannot tell which copy is the good one |
| Snapshots | Yes, instant and cheap | No (needs an external tool) |
| Compression | Yes, transparent (`zstd`) | No |
| Simplicity | A few more concepts to learn (subvolumes, scrub) | Very simple, classic tools |
| Failed disk | Must mount with `degraded`, then `btrfs replace` | Boots in degraded mode, then `mdadm --add` |
| Reading a single disk on another PC | Linux with Btrfs | Linux with mdadm |
| Cockpit interface | Works, but shows a cosmetic warning for the second disk | Well supported |
| Maturity | Stable for a 2-disk mirror (avoid RAID 5/6) | Very mature |

**Recommendation**: for family photos and documents you want to keep for a long time, **Btrfs RAID 1** is the best choice, thanks to the detection and repair of silent corruption and to snapshots. Choose mdadm + ext4 if you prefer maximum simplicity.

**In both cases**:

- RAID protects against the failure of one disk, not against accidental deletion, a virus or a failure of the enclosure. Keep an **external backup** of important data.
- The NVMe disks are for data storage only. The CM4 cannot boot from them through the PCIe switch: the system stays on the eMMC.
- The `wipefs` and creation commands **erase the disks**. Check the disk names with `lsblk` before running them.

Before starting, check that both SSDs are installed ([step 7 of the CM4 setup](./Setup-CM4-NVME-NAS-BOX.md#7-add-the-m2-disks)) and recognized:

```
lsblk -o NAME,SIZE,MODEL,MOUNTPOINT
sudo wipefs /dev/nvme0n1 /dev/nvme1n1
```

The second command lists the existing signatures without erasing anything.

## 2. Option A: Btrfs RAID 1

### Install the tools

```
sudo apt update
sudo apt install btrfs-progs btrfsmaintenance
```

### Create the mirror (erases the disks)

```
sudo wipefs -a /dev/nvme0n1 /dev/nvme1n1
sudo mkfs.btrfs -L nas -m raid1 -d raid1 /dev/nvme0n1 /dev/nvme1n1
```

`-m raid1 -d raid1` mirrors both metadata and data. No partition table is needed: Btrfs uses the whole disks.

### Mount and create the subvolumes

```
sudo mkdir -p /mnt/nas
sudo mount -o noatime,compress=zstd:1 /dev/nvme0n1 /mnt/nas
sudo btrfs subvolume create /mnt/nas/photos
sudo btrfs subvolume create /mnt/nas/documents
sudo btrfs subvolume create /mnt/nas/videos
sudo mkdir /mnt/nas/.snapshots
```

A subvolume behaves like a folder, but can have its own snapshots, independent of the others. `compress=zstd:1` is the lightest compression level, suited to the CM4; Btrfs automatically skips incompressible data such as JPEGs and videos.

### Permanent mount

Get the filesystem UUID:

```
sudo blkid /dev/nvme0n1 /dev/nvme1n1
```

Both disks show the **same `UUID`** and different `UUID_SUB` values. Use only `UUID` (not `UUID_SUB`) in `/etc/fstab`:

```
UUID=<the_uuid>  /mnt/nas  btrfs  defaults,noatime,compress=zstd:1,nofail  0  0
```

Test before rebooting:

```
sudo umount /mnt/nas
sudo systemctl daemon-reload
sudo mount -a
findmnt /mnt/nas
```

`nofail` lets the system boot even if the disks are absent.

### Automatic maintenance

Edit `/etc/default/btrfsmaintenance`:

```
BTRFS_SCRUB_MOUNTPOINTS="/mnt/nas"
BTRFS_SCRUB_PERIOD="monthly"
```

Then:

```
sudo systemctl restart btrfsmaintenance-refresh.service
sudo systemctl enable --now fstrim.timer
```

The monthly scrub reads every block, checks its checksum and repairs corrupted blocks from the other disk.

### Create the family user

The `family` Linux account and group will own the `photos` and `videos` subvolumes (see [Setup file shares](./Setup-file-shares.md), section 3). Create it with limited rights: no home folder, no shell login and no `sudo`, so it can only be used through Samba. `useradd` also creates a `family` group with the same name:

```
sudo useradd -M -s /usr/sbin/nologin family
```

Add `pi` to the `family` group, so it can manage `photos` and `videos` over SSH:

```
sudo usermod -aG family pi
```

Replace `pi` with the user name you chose in `userconf.txt`. Log out and back in for the new group to apply (check with `id pi`).

### Directories and subvolumes

Two kinds of folders exist on the volume:

- **Subvolumes** (`photos`, `documents`, `videos`): the top-level folders. Each one is snapshotted on its own and matches a Samba share. Create one for each new kind of content you want to share or snapshot separately.
- **Ordinary directories**, inside a subvolume: to organize the content (`photos/2026`, `videos/holidays`, ...). They are included in the snapshots of their subvolume.

A directory cannot be turned into a subvolume later. To do it, create the subvolume and copy the content into it (see the end of this section).

**Create a directory** inside `photos` or `videos` from the command line (as `pi`, a member of the `family` group, see [Create the family user](#create-the-family-user)):

```
mkdir -m 2775 /mnt/nas/photos/2026
```

`-m 2775` keeps the folder writable by the `family` group and the setgid bit (new files inherit the `family` group). A plain `mkdir` would create it with mode `2755` (because of the default `umask 022`), and `family` could not write to it through Samba. Folders created through Samba by `family` get the right mode automatically.

In `documents` (owned by `pi`), a plain `mkdir /mnt/nas/documents/<name>` is enough.

**Create a subvolume**, for example for music:

```
sudo btrfs subvolume create /mnt/nas/music
sudo chown family:family /mnt/nas/music
sudo chmod 2775 /mnt/nas/music
```

Then add it to the automatic snapshots (`subvolume music` in `btrbk.conf`, below) and, if needed, create a Samba share for it, like `[photos]` in [Setup file shares](./Setup-file-shares.md).

**List the subvolumes**:

```
sudo btrfs subvolume list -o /mnt/nas
```

`-o` lists only the subvolumes directly under `/mnt/nas`, not the snapshots in `.snapshots`.

**Turn an existing directory into a subvolume** (for example `photos/scans`):

```
sudo mv /mnt/nas/photos/scans /mnt/nas/photos/scans.old
sudo btrfs subvolume create /mnt/nas/photos/scans
sudo cp -a --reflink=always /mnt/nas/photos/scans.old/. /mnt/nas/photos/scans/
sudo rm -r /mnt/nas/photos/scans.old
```

`--reflink=always` clones the files instead of copying the data: it is fast and uses no extra space. Check the copy before the `rm`. Note that a subvolume nested inside `photos` is **not** included in the snapshots of `photos`: snapshot it separately.

**Delete a subvolume** (erases its content):

```
sudo btrfs subvolume delete /mnt/nas/music
```

### Snapshots

A snapshot is a read-only, frozen copy of a subvolume at a given time. It is created instantly and only uses space for the data changed or deleted since. Snapshots protect against accidental deletion or modification, but they are on the same disks: they are **not a backup**.

The snapshots are stored in `/mnt/nas/.snapshots` and named `<subvolume>-<date>`, the naming expected by the Samba "Previous Versions" settings ([Setup file shares](./Setup-file-shares.md), section 6). If you use `btrbk` (below), use its naming instead and adapt `shadow:format`.

**Create a snapshot** of one subvolume:

```
sudo btrfs subvolume snapshot -r /mnt/nas/photos /mnt/nas/.snapshots/photos-$(date +%F)
```

Of all the subvolumes at once:

```
for sv in photos documents videos; do
  sudo btrfs subvolume snapshot -r /mnt/nas/$sv /mnt/nas/.snapshots/$sv-$(date +%F)
done
```

`-r` makes the snapshot read-only, so it cannot be modified by mistake. With this daily naming, a second snapshot on the same day fails because the name already exists.

**List the snapshots**:

```
ls /mnt/nas/.snapshots
sudo btrfs subvolume list -s /mnt/nas
```

**Restore a file or a folder**: copy it from the snapshot:

```
cp -a --reflink=always /mnt/nas/.snapshots/photos-2026-10-06/2026/IMG_0001.jpg /mnt/nas/photos/2026/
```

From Windows, the Samba "Previous Versions" tab does the same.

**Restore a whole subvolume**: rename the current one, then create a writable snapshot from the saved one in its place:

```
sudo mv /mnt/nas/photos /mnt/nas/photos.broken
sudo btrfs subvolume snapshot /mnt/nas/.snapshots/photos-2026-10-06 /mnt/nas/photos
```

Without `-r`, the new snapshot is writable and replaces the subvolume. Check the content, then delete the old one: `sudo btrfs subvolume delete /mnt/nas/photos.broken`.

**Delete a snapshot**:

```
sudo btrfs subvolume delete /mnt/nas/.snapshots/photos-2026-09-01
```

The space is freed in the background, a few seconds to minutes later.

### Automatic snapshots (btrbk)

`btrbk` creates the snapshots on a schedule and deletes the old ones according to a retention policy.

```
sudo apt install btrbk
```

Create `/etc/btrbk/btrbk.conf`:

```
timestamp_format        long
snapshot_preserve_min   2d
snapshot_preserve       14d 8w 12m

volume /mnt/nas
  snapshot_dir .snapshots
  subvolume photos
  subvolume documents
  subvolume videos
```

- `timestamp_format long`: snapshots are named `photos.20261006T0300`. In the Samba settings, use `shadow:format = photos.%Y%m%dT%H%M` (and the same for the other shares).
- `snapshot_preserve_min 2d`: all snapshots of the last 2 days are kept.
- `snapshot_preserve 14d 8w 12m`: then one snapshot per day for 14 days, one per week for 8 weeks, and one per month for 12 months. Older snapshots are deleted.

Test without changing anything, then run it once:

```
sudo btrbk dryrun
sudo btrbk run
sudo btrbk list snapshots
```

Schedule it daily with the systemd timer shipped with the package:

```
sudo systemctl enable --now btrbk.timer
systemctl list-timers btrbk.timer
```

If the timer does not exist, add a daily cron job instead: create `/etc/cron.daily/btrbk` containing `#!/bin/sh` and `exec /usr/bin/btrbk -q run`, then `sudo chmod +x /etc/cron.daily/btrbk`.

Use **either** the manual naming **or** btrbk's: Samba "Previous Versions" only shows the snapshots that match its `shadow:format`. To take an extra snapshot by hand with btrbk (before a large reorganization, for example), run `sudo btrbk run`.

`btrbk` can also send the snapshots to an external disk for backups (`target` option, see its documentation).

### Checks

```
sudo btrfs filesystem show /mnt/nas
sudo btrfs filesystem usage /mnt/nas
sudo btrfs device stats /mnt/nas
sudo btrfs scrub start -B /mnt/nas
```

`device stats` must show zeros everywhere. Non-zero values indicate write, read or corruption errors.

### Failed disk

- To access the data with the remaining disk: `sudo mount -o degraded,ro /dev/nvmeXn1 /mnt/nas`.
- To replace the failed disk: mount the volume with `degraded`, connect the new disk, then `sudo btrfs replace start <old_devid> /dev/nvmeXn1 /mnt/nas`. The `devid` is shown by `btrfs filesystem show`.
- **Convert back to RAID 1 afterwards.** While the volume is mounted degraded and read-write, new data may be written in the `single` profile (not mirrored). Once the replacement is finished, run:

  ```
  sudo btrfs balance start -dconvert=raid1,soft -mconvert=raid1,soft /mnt/nas
  sudo btrfs filesystem usage /mnt/nas
  ```

  `usage` must show only `RAID1` for data and metadata, with no `single` line.

### Cockpit

Cockpit may show the "Inconsistent filesystem mount" warning for the second disk. This is a known display problem with multi-device Btrfs volumes, with no effect on the volume. Do not click "Mount" or "Fix" in this case.

## 3. Option B: mdadm RAID 1 + ext4

Use this **instead of** option A, not in addition to it.

### Install mdadm

```
sudo apt install --no-install-recommends mdadm
```

`--no-install-recommends` avoids installing an unnecessary mail server.

### Create the mirror (erases the disks)

```
sudo wipefs -a /dev/nvme0n1 /dev/nvme1n1
sudo mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/nvme0n1 /dev/nvme1n1
```

Answer `y` if mdadm asks for confirmation. The initial synchronization runs in the background and you can already use the array. Follow it with:

```
cat /proc/mdstat
```

### Format and mount

```
sudo mkfs.ext4 -m 0 -L nas /dev/md0
sudo mkdir -p /mnt/nas
sudo mount /dev/md0 /mnt/nas
```

- `-m 0` reserves no space for root (5 % by default).
- The default inode density is kept on purpose: it handles many small documents. If the NAS only holds large photos and videos, you can add `-T largefile` to reduce the inode table, then check with `df -i` that you stay far from the limit.

### Make the array permanent

```
sudo mdadm --detail --scan | sudo tee -a /etc/mdadm/mdadm.conf
sudo update-initramfs -u
```

Get the filesystem UUID (`sudo blkid /dev/md0`) and add to `/etc/fstab`:

```
UUID=<the_uuid>  /mnt/nas  ext4  defaults,noatime,nofail  0  2
```

Test before rebooting:

```
sudo umount /mnt/nas
sudo systemctl daemon-reload
sudo mount -a
findmnt /mnt/nas
```

### Monitoring

```
sudo mdadm --detail /dev/md0
cat /proc/mdstat
```

`[UU]` means both disks are in sync. `[U_]` indicates a degraded array. Debian already schedules a monthly consistency check (`checkarray`). Check that it is active with `systemctl list-timers | grep -i mdcheck` or the `/etc/cron.d/mdadm` file.

To be notified automatically, configure `MAILADDR` in `/etc/mdadm/mdadm.conf` and have a mail service available, or write a small script that checks `/proc/mdstat`.

### Failed disk

The array keeps working in degraded mode on the remaining disk. To replace the failed disk:

```
sudo mdadm /dev/md0 --fail /dev/nvmeXn1 --remove /dev/nvmeXn1
# connect the new disk, then:
sudo mdadm /dev/md0 --add /dev/nvmeXn1
```

The new disk must be at least as large as the smaller of the two original disks.

To read the surviving disk on its own (for example on a Linux PC with mdadm):

```
sudo mdadm --assemble --run /dev/md0 /dev/nvmeXn1
sudo mount /dev/md0 /mnt/nas
```

### Option: 1.0 metadata

With the default configuration (1.2 metadata), the disk cannot be mounted directly with `mount` outside of mdadm. If you want to be able to read a single disk without mdadm, create the array with `--metadata=1.0`:

```
sudo mdadm --create /dev/md0 --level=1 --raid-devices=2 --metadata=1.0 /dev/nvme0n1 /dev/nvme1n1
```

Then only mount a single disk read-only (`mount -o ro`): writing to it directly would desynchronize it from the other one.

## 4. Next steps

After choosing the filesystem: network shares (see [Setup file shares](./Setup-file-shares.md)) and external backup.

## 5. Sources

The directory, snapshot and `btrbk` procedures were written from general knowledge of these tools and have not yet been tested on this NAS. Check them against the documentation:

- [btrfs-subvolume(8)](https://btrfs.readthedocs.io/en/latest/btrfs-subvolume.html): `create`, `snapshot -r`, `list`, `delete`.
- [btrbk](https://digint.ch/btrbk/) and its [btrbk.conf(5)](https://digint.ch/btrbk/doc/btrbk.conf.5.html) manual page: `timestamp_format`, `snapshot_preserve`, `snapshot_dir`, `target`.

- [raspberrypi/firmware issue #1684](https://github.com/raspberrypi/firmware/issues/1684): the CM4 bootloader cannot boot from an NVMe behind a PCIe switch/bridge, which is why the system stays on the eMMC.
- [cockpit-project/cockpit issue #20743](https://github.com/cockpit-project/cockpit/issues/20743): Cockpit shows the second device of a Btrfs RAID 1 as unmounted. A related discussion: [Fedora Discussion](https://discussion.fedoraproject.org/t/cockpit-shows-warning-for-disks-in-btrfs-raid-1-array/78039) (found through search, not read in full).
