# RPi NAS & Energy Hub

## Purpose

The goal of this personal project is to build a home NAS and Energy controller with:

- Raspberry Pi Compute Module 4
- Wireshare CM4-NVME-NAS-BOX dual M.2 motherboard
- two SSDs in a mirror (RAID 1)
- P1 (RJ12 to USB) cable for Iskra energy meter.

This single, low-power machine can then:

- serve as a local NAS (network file storage)
- serve as a media server
- read and share the home's energy meter live data (P1)
- run Home Assistant (Docker)
- access PV (solar) production
- control EV chargers using energy production and consumption

## Sections

1. **Hardware setup**: [Setup the Raspberry CM4 with Raspbian OS on the Wireshare CM4-NVME-NAS-BOX](./Setup-CM4-NVME-NAS-BOX.md)

2. **NAS setup**: [Setup the NAS disks (Btrfs or mdadm RAID 1)](./Setup-NAS-disks.md)

3. **File shares**: [Setup Samba file shares (family read-write, guest read-only)](./Setup-file-shares.md)
