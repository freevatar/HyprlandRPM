# Hyprland RPMs

Credit to https://github.com/solopasha - original packaging comes from his https://github.com/solopasha/hyprlandRPM.

This repo has since changed a lot:
* added update and build tools with automatic dependency graph resolution
* bash completion
* package list cut to Hypr-only packages
* many specs refactored

## Description

This repo packages `Hyprland 0.57.1` for `Fedora 44` and `Fedora 45`.
Fedora 45 support is being validated; Fedora 44 remains supported during testing.

## Install

Enable the [COPR repository](https://copr.fedorainfracloud.org/coprs/giperborey/Hyprland/) and install stable Hyprland release:

```sh
sudo dnf copr enable giperborey/Hyprland
sudo dnf install hyprland xdg-desktop-portal-hyprland
```

Use `hyprland-git` instead if you prefer latest commits.

## Updates

Hyprland updates are checked every six hours.

See the [maintenance guide](docs/maintenance.md) for updating packages, running checks, and setting up builds.
