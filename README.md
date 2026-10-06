# RPi NAS

## Purpose

The goal of this personal project is to build a home server on a Raspberry Pi Compute Module 4, with two SSDs in a mirror (RAID 1). Connected to the home main energy meter P1 port with a USB-P1 cable, this single, low-power machine can then:

- serve as a local NAS (network file storage for the family)
- serve as a media server
- read and share the energy meter's live data
- control EV chargers using energy meter to consume live PV (solar) production
- run Home Assistant (in Docker)
