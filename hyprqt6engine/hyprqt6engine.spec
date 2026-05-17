Name:           hyprqt6engine
Version:        0.1.0
Release:        %autorelease -b14
Summary:        Qt6 theme provider for Hyprland

License:        BSD-3-Clause
URL:            https://github.com/hyprwm/hyprqt6engine
Source0:        %{url}/archive/refs/tags/v%{version}/%{name}-%{version}.tar.gz

# Needed for the v0.1.0 tag when building with Qt 6.9 private Gui targets.
Patch0:         fix-build.diff

# https://fedoraproject.org/wiki/Changes/EncourageI686LeafRemoval
ExcludeArch:    %{ix86}

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  pkgconf-pkg-config
BuildRequires:  qt6-rpm-macros

BuildRequires:  pkgconfig(hyprlang)
BuildRequires:  pkgconfig(hyprutils)

BuildRequires:  cmake(KF6ColorScheme)
BuildRequires:  cmake(KF6Config)
BuildRequires:  cmake(KF6IconThemes)

BuildRequires:  cmake(Qt6BuildInternals) >= 6.11
BuildRequires:  cmake(Qt6Core) >= 6.11
BuildRequires:  cmake(Qt6Gui) >= 6.11
BuildRequires:  cmake(Qt6GuiPrivate) >= 6.11
BuildRequires:  cmake(Qt6Widgets) >= 6.11
BuildRequires:  qt6-qtbase-private-devel

%description
%{summary}.

%prep
%autosetup -p1

%build
%cmake \
    -DCMAKE_BUILD_TYPE=Release \
    -DPLUGINDIR=%{_qt6_plugindir}
%cmake_build

%install
%cmake_install

%files
%license LICENSE
%doc README.md
%{_libdir}/libhyprqt6engine-common.so
%{_qt6_plugindir}/platformthemes/libhyprqt6engine.so
%{_qt6_plugindir}/styles/libhypr-style.so

%changelog
%autochangelog
