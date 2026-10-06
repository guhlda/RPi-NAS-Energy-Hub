# Setup file shares (Samba)

## What this document covers

This document covers sharing the NAS volume on the local network with Samba (SMB), for Windows, macOS and Linux computers:

- one share per Btrfs subvolume (`photos`, `documents`, `videos`),
- a `family` account and group, which own `photos` and `videos` and have **read-write** access to them,
- a `guest` account with **read-only** access to all shares,
- the `pi` account (the user created with `userconf.txt`) has **no Samba account**: it administers the NAS over SSH only (it is a member of the `family` group, so it can also write to `photos` and `videos` from the command line),
- Btrfs snapshots shown as **"Previous Versions"** in Windows Explorer.

Prerequisite: the NAS volume is set up with **Btrfs RAID 1** and mounted on `/mnt/nas`, with the `photos`, `documents` and `videos` subvolumes, the `/mnt/nas/.snapshots` folder and the `family` user, as described in [Setup NAS disks](./Setup-NAS-disks.md) (option A). If you chose mdadm + ext4, follow the same procedure but skip the Btrfs-specific parts (`vfs objects`, section 6).

## TOC

1. [Install Samba](#1-install-samba)
2. [Accounts](#2-accounts)
3. [Folder permissions](#3-folder-permissions)
4. [Configure the shares](#4-configure-the-shares)
5. [Check the shares](#5-check-the-shares)
6. [Previous Versions (Btrfs snapshots)](#6-previous-versions-btrfs-snapshots)
7. [Connect from a computer](#7-connect-from-a-computer)
8. [Notes](#8-notes)
9. [Sources](#9-sources)

## 1. Install Samba

```
sudo apt update
sudo apt install samba samba-vfs-modules
```

`samba-vfs-modules` provides the `btrfs` and `shadow_copy2` modules used below. The Samba services (`smbd`, `nmbd`) start automatically and run at every boot.

## 2. Accounts

Samba uses Linux accounts, but with its own password database: each user also needs a **Samba password**, which can differ from their Linux password.

**`family` (read-write on `photos` and `videos`)**: the Linux account and group are created in [Setup NAS disks](./Setup-NAS-disks.md#create-the-family-user). Only add its Samba password:

```
sudo smbpasswd -a family
```

**`guest` (read-only)**: create a Linux account without a home folder and without shell login, so it can only be used through Samba:

```
sudo useradd -M -s /usr/sbin/nologin guest
sudo smbpasswd -a guest
```

`guest` here is a normal account with a password, not Samba's anonymous guest access: connections without a valid account stay refused (`map to guest = never`, section 4).

**`pi` (no Samba access)**: do not add it to Samba. It keeps its SSH access for administration, and the shares stay reachable only with `family` and `guest`. Replace `pi` with the user name you chose in `userconf.txt`.

To change a Samba password later, run `sudo smbpasswd <user>`. To list the Samba accounts: `sudo pdbedit -L`.

## 3. Folder permissions

The subvolumes were created with `sudo`, so they belong to `root`.

**`photos` and `videos`**: owned by `family`, with write rights for the `family` group:

```
sudo chown -R family:family /mnt/nas/photos /mnt/nas/videos
sudo chmod -R g+w,o+rX /mnt/nas/photos /mnt/nas/videos
sudo find /mnt/nas/photos /mnt/nas/videos -type d -exec chmod g+s {} +
```

- `g+w`: the `family` group (`family`, and `pi` over SSH) can write.
- `o+rX`: other users (`guest`) can read files and open folders, but not write.
- `g+s` on folders (setgid): new files and folders inherit the `family` group, whoever creates them. Folders end up with mode `2775`.

**`documents`**: owned by `pi`, readable by the others. Through Samba it is read-only for everyone; `pi` adds files over SSH (for example with `scp` or `rsync`):

```
sudo chown -R pi:pi /mnt/nas/documents
sudo chmod 755 /mnt/nas/documents
```

Samba checks both its own share settings and these Linux permissions. `guest` only gets the "others" read permission, so it cannot write anywhere, even if a share setting were wrong.

## 4. Configure the shares

Keep a copy of the original configuration, then edit it:

```
sudo cp /etc/samba/smb.conf /etc/samba/smb.conf.orig
sudo nano /etc/samba/smb.conf
```

In the `[global]` section, leave the defaults and check or add:

```
[global]
   workgroup = WORKGROUP
   server role = standalone server
   map to guest = never
```

`map to guest = never` refuses connections without a valid account (no guest access).

The default file ends with `[homes]`, `[printers]` and `[print$]` sections. Comment them out (`;` at the start of each line) or delete them: they are not needed on the NAS.

Add one section per share at the end of the file:

```
[photos]
   path = /mnt/nas/photos
   valid users = @family guest
   read only = yes
   write list = @family
   force group = family
   create mask = 0664
   directory mask = 2775
   vfs objects = btrfs

[documents]
   path = /mnt/nas/documents
   valid users = family guest
   read only = yes
   vfs objects = btrfs

[videos]
   path = /mnt/nas/videos
   valid users = @family guest
   read only = yes
   write list = @family
   force group = family
   create mask = 0664
   directory mask = 2775
   vfs objects = btrfs
```

- `valid users`: only these accounts can open the share. `@family` means all members of the `family` group; `pi` is a member but has no Samba password, so it cannot connect.
- `read only = yes` + `write list`: everyone reads; only the `family` group writes to `photos` and `videos`. `documents` has no `write list`, so it is read-only for all Samba users.
- `force group = family`: files created through Samba always get the `family` group.
- `create mask` / `directory mask`: in `photos` and `videos`, new files (`0664`) and folders (`2775`) stay writable by the `family` group and readable by `guest`.
- `vfs objects = btrfs`: when a file is copied inside the NAS (from one folder or share to another), Samba asks Btrfs to clone it instead of copying the data. The copy is almost instant and uses no extra space until one of the two copies is modified.

Btrfs-specific choices:

- **Share the subvolumes, not `/mnt/nas`**: this keeps the `.snapshots` folder out of the shares, and each share matches a subvolume with its own snapshots.
- **Compression** (`compress=zstd:1`) is transparent: nothing to configure in Samba.

Check the configuration, then restart Samba:

```
testparm
sudo systemctl restart smbd nmbd
```

`testparm` reports syntax errors and prints the effective configuration.

## 5. Check the shares

From the Raspberry Pi itself:

```
smbclient -L localhost -U family
```

The list must show `photos`, `documents` and `videos`.

Check that `guest` can read but not write:

```
smbclient //localhost/photos -U guest -c 'ls'
smbclient //localhost/photos -U guest -c 'put /etc/hostname test.txt'
```

The first command lists the files. The second must fail with `NT_STATUS_ACCESS_DENIED`.

Check that `family` can write, and that the new file gets the right owner and group:

```
smbclient //localhost/photos -U family -c 'put /etc/hostname test.txt'
ls -l /mnt/nas/photos/test.txt
smbclient //localhost/photos -U family -c 'rm test.txt'
```

`ls -l` must show `-rw-rw-r-- ... family family`. The same `put` command with `-U family` must fail in `documents`, and any command with `-U pi` must fail with `NT_STATUS_LOGON_FAILURE`.

## 6. Previous Versions (Btrfs snapshots)

The `shadow_copy2` module shows the read-only Btrfs snapshots in the **Previous Versions** tab of Windows Explorer (right-click a file or folder, Properties). Users can open or copy an older version of a file without access to the Raspberry Pi. `guest` can open and copy old versions; only `family` can restore them in place, in `photos` and `videos`.

The module must know where the snapshots are and how they are named. With the naming of [Setup NAS disks](./Setup-NAS-disks.md) (`/mnt/nas/.snapshots/photos-2026-10-06`), change the `vfs objects` line and add the `shadow:` lines in each share. For `[photos]`:

```
[photos]
   ...
   vfs objects = btrfs shadow_copy2
   shadow:snapdir = /mnt/nas/.snapshots
   shadow:basedir = /mnt/nas/photos
   shadow:format = photos-%Y-%m-%d
   shadow:localtime = yes
   shadow:sort = desc
```

- `shadow:snapdir`: the folder that contains the snapshots.
- `shadow:basedir`: the share's subvolume. The subvolumes are not separate mount points, so this tells Samba that each snapshot is a copy of `/mnt/nas/photos`, not of the whole `/mnt/nas`.
- `shadow:format`: the snapshot name pattern. Only the snapshots of this subvolume match it (`photos-...`), so the snapshots of the other subvolumes are ignored.
- `shadow:localtime = yes`: the date in the name is local time, not UTC.

For `[documents]` and `[videos]`, use `shadow:basedir = /mnt/nas/documents` and `shadow:format = documents-%Y-%m-%d`, and so on.

If you automate the snapshots with `btrbk`, adapt `shadow:format` to its naming (by default `photos.20261006T0300`, so `shadow:format = photos.%Y%m%dT%H%M`).

Then `testparm` and `sudo systemctl restart smbd`. Take a snapshot, and check that it appears in the Previous Versions tab of a Windows computer.

Previous Versions works only from Windows. macOS and Linux file managers do not show it; `pi` can still browse the snapshots over SSH in `/mnt/nas/.snapshots`.

## 7. Connect from a computer

Use the host name (`raspi`) or the IP address of the Raspberry Pi, and log in with `family` or `guest` and its Samba password.

- **Windows**: in File Explorer, type `\\raspi\photos` in the address bar. To keep it, right-click "This PC", "Map network drive".
- **macOS**: in the Finder, "Go", "Connect to Server", then `smb://raspi/photos`.
- **Linux**: in the file manager, "Other Locations", then `smb://raspi/photos`.

**Discovery**: macOS and Linux find the NAS in the network list through Avahi (installed by default on Raspberry Pi OS). For Windows to show it under "Network", install `wsdd`:

```
sudo apt install wsdd
```

Without it, typing `\\raspi` in the address bar still works.

## 8. Notes

- The shares are open on the local network only. No firewall is active by default on Raspberry Pi OS. **Do not expose the SMB ports (139, 445) to the Internet**; use a VPN for remote access.
- Samba requires SMB2 or later by default. Leave it that way: do not enable SMB1 (`server min protocol = NT1`), even if an old device (some TVs) asks for it. For a TV, a media server (DLNA, Jellyfin) is the better option.
- To see who is connected: `sudo smbstatus`.

## 9. Sources

The configuration was written from general knowledge of Samba and has not yet been tested on this NAS, in particular the `shadow_copy2` settings (section 6). Check them against the manual pages:

- [smb.conf(5)](https://www.samba.org/samba/docs/current/man-html/smb.conf.5.html): `valid users`, `read only`, `write list`, `force group`, `create mask`, `map to guest`.
- [vfs_btrfs(8)](https://www.samba.org/samba/docs/current/man-html/vfs_btrfs.8.html): server-side copy with Btrfs clones.
- [vfs_shadow_copy2(8)](https://www.samba.org/samba/docs/current/man-html/vfs_shadow_copy2.8.html): `shadow:snapdir`, `shadow:basedir`, `shadow:format`, `shadow:localtime`.
- [smbpasswd(8)](https://www.samba.org/samba/docs/current/man-html/smbpasswd.8.html): Samba password database.
