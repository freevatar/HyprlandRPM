Name:           hyprpaper
Version:        0.8.4
Release:        %autorelease -b5
Summary:        Blazing fast wayland wallpaper utility with IPC controls

# LICENSE: BSD-3-Clause
# protocols/wlr-layer-shell-unstable-v1.xml: HPND-sell-variant
License:        BSD-3-Clause AND HPND-sell-variant
URL:            https://github.com/hyprwm/hyprpaper
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# https://fedoraproject.org/wiki/Changes/EncourageI686LeafRemoval
ExcludeArch:    %{ix86}

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  systemd-rpm-macros

BuildRequires:  cmake(hyprwayland-scanner) >= 0.4.0
BuildRequires:  pkgconfig(glesv2)
BuildRequires:  pkgconfig(hyprlang) >= 0.6.0
BuildRequires:  pkgconfig(hyprtoolkit) >= 0.4.1
BuildRequires:  pkgconfig(hyprutils) >= 0.2.4
BuildRequires:  pkgconfig(hyprwire)
BuildRequires:  pkgconfig(libdrm)
BuildRequires:  pkgconfig(libmagic)
BuildRequires:  pkgconfig(opengl)
BuildRequires:  pkgconfig(pixman-1)
BuildRequires:  pkgconfig(wayland-client)
BuildRequires:  pkgconfig(wayland-cursor)
BuildRequires:  pkgconfig(wayland-protocols)
BuildRequires:  pkgconfig(wayland-scanner)

%description
A blazing fast wayland wallpaper utility.

%prep
%autosetup -p1

%build
%cmake
%cmake_build

%install
%cmake_install

%post
%systemd_user_post %{name}.service

%preun
%systemd_user_preun %{name}.service

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_userunitdir}/%{name}.service

%changelog
%autochangelog
