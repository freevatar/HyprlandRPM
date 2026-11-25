%global commit 5ffc887ff3154d6b7b1ff25180ce17f05b414dff

Name:           xdg-desktop-portal-hyprland
Epoch:          1
Version:        1.5.0^28.git%{sub %{commit} 1 7}
Release:        %autorelease
Summary:        XDG Desktop Portal backend for Hyprland

License:        BSD-3-Clause
URL:            https://github.com/hyprwm/xdg-desktop-portal-hyprland
Source0:        %{url}/archive/%{commit}/%{name}-%{commit}.tar.gz

BuildRequires:  cmake >= 3.19
BuildRequires:  gcc-c++
BuildRequires:  systemd-rpm-macros

BuildRequires:  pkgconfig(gbm)
BuildRequires:  pkgconfig(hyprland-protocols) >= 0.8.0
BuildRequires:  pkgconfig(hyprlang) >= 0.2.0
BuildRequires:  pkgconfig(hyprtoolkit) >= 0.7.0
BuildRequires:  pkgconfig(hyprutils) >= 0.14.4
BuildRequires:  pkgconfig(hyprwayland-scanner) >= 0.4.2
BuildRequires:  pkgconfig(libdrm)
BuildRequires:  pkgconfig(libpipewire-0.3) >= 1.1.82
BuildRequires:  pkgconfig(libspa-0.2)
BuildRequires:  pkgconfig(sdbus-c++) >= 2.0.0
BuildRequires:  pkgconfig(uuid)
BuildRequires:  pkgconfig(wayland-client)
BuildRequires:  pkgconfig(wayland-protocols) >= 1.39
BuildRequires:  pkgconfig(wayland-scanner)

Requires:       grim
Requires:       shared-mime-info
Requires:       slurp
Requires:       xdg-desktop-portal
Recommends:     hyprpicker

Enhances:       hyprland
Supplements:    hyprland
Supplements:    hyprland-git

%description
%{name} is an XDG Desktop Portal backend for Hyprland. It provides
Hyprland-specific portal implementations for desktop integration, including
file selection, screen sharing, and screenshots.

%prep
%autosetup -n %{name}-%{commit}

%build
%cmake \
    -DSYSTEMD_SERVICES:BOOL=ON
%cmake_build

%install
%cmake_install

%check
%ctest

%post
%systemd_user_post %{name}.service

%preun
%systemd_user_preun %{name}.service

%files
%license LICENSE
%doc README.md
%{_bindir}/hyprland-file-picker
%{_bindir}/hyprland-share-picker
%{_libexecdir}/%{name}
%{_datadir}/dbus-1/services/org.freedesktop.impl.portal.desktop.hyprland.service
%{_datadir}/xdg-desktop-portal/portals/hyprland.portal
%{_userunitdir}/%{name}.service

%changelog
%autochangelog
