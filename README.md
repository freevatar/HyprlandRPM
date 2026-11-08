# Hyprland RPMs

Credit to https://github.com/solopasha - original packaging comes from his https://github.com/solopasha/hyprlandRPM.

This repo has since changed a lot:
* added update and build tools with automatic dependency graph resolution
* bash completion
* package list cut to Hypr-only packages
* many specs refactored

## Description

This repo packages `hyprland-git` snapshots and companion packages for `Fedora 45`.

## Install

Enable the [COPR repository](https://copr.fedorainfracloud.org/coprs/giperborey/Hyprland/) and install Hyprland:
1. `sudo dnf copr enable giperborey/Hyprland`
2. `sudo dnf in hyprland-git`

## Updates

Hyprland updates are checked every six hours.

See the [maintenance guide](docs/maintenance.md) for updating packages, running checks, and setting up builds.
