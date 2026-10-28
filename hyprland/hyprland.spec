%global upstream_version 0.57.0
%global hyprland_commit 0aa6745f705f2c4b0a5004eb019ef76e5bbdb426

%global lua54_compat 0
%global lua_min_version 5.5
%global lua_next_version 5.6
%if 0%{?fedora} && 0%{?fedora} < 45
%global lua54_compat 1
%global lua_min_version 5.4
%global lua_next_version 5.5
%endif

Name:           hyprland
Version:        %{upstream_version}
Release:        %autorelease -b2
Summary:        Dynamic tiling Wayland compositor that doesn't sacrifice on its looks

# Hyprland: BSD-3-Clause
# hyprland-protocols: BSD-3-Clause
# subprojects/udis86: BSD-2-Clause
# protocols/ext-workspace-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-foreign-toplevel-management-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-layer-shell-unstable-v1.xml: HPND-sell-variant
# protocols/idle.xml: LGPL-2.1-or-later
License:        BSD-3-Clause AND BSD-2-Clause AND HPND-sell-variant AND LGPL-2.1-or-later
URL:            https://github.com/hyprwm/Hyprland
Source0:        %{url}/releases/download/v%{upstream_version}/source-v%{upstream_version}.tar.gz
Source1:        macros.hyprland
Patch0:         escape-version-metadata.patch
Patch1:         lua54-hyprpm.patch

# Build tools
BuildRequires:  cmake >= 3.30
BuildRequires:  gcc-c++
# Retain the Glaze 7.x series used by this package.
BuildRequires:  glaze-static >= 7
BuildRequires:  glaze-static < 8
BuildRequires:  ninja-build
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

# Hypr ecosystem
BuildRequires:  pkgconfig(aquamarine) >= 0.15.0
BuildRequires:  pkgconfig(hyprcursor) >= 0.1.7
BuildRequires:  pkgconfig(hyprgraphics) >= 0.5.1
BuildRequires:  pkgconfig(hyprland-protocols) >= 0.8.0
BuildRequires:  pkgconfig(hyprlang) >= 0.6.7
BuildRequires:  pkgconfig(hyprutils) >= 0.14.3
BuildRequires:  pkgconfig(hyprwayland-scanner) >= 0.3.10
BuildRequires:  pkgconfig(hyprwire)

# Core compositor and utility dependencies
BuildRequires:  muParser-devel
BuildRequires:  pkgconfig(cairo)
BuildRequires:  pkgconfig(egl)
BuildRequires:  pkgconfig(gbm)
BuildRequires:  pkgconfig(gio-2.0)
BuildRequires:  pkgconfig(glesv2)
BuildRequires:  pkgconfig(glslang)
BuildRequires:  pkgconfig(lcms2)
BuildRequires:  pkgconfig(libcanberra)
BuildRequires:  pkgconfig(libdrm)
BuildRequires:  pkgconfig(libeis-1.0)
BuildRequires:  pkgconfig(libinput) >= 1.29
BuildRequires:  pkgconfig(pango)
BuildRequires:  pkgconfig(pangocairo)
BuildRequires:  pkgconfig(pixman-1)
BuildRequires:  pkgconfig(re2)
BuildRequires:  pkgconfig(readline)
BuildRequires:  pkgconfig(sdbus-c++) >= 2.0.0
BuildRequires:  pkgconfig(tomlplusplus)
BuildRequires:  pkgconfig(uuid)
BuildRequires:  pkgconfig(wayland-protocols) >= 1.49
BuildRequires:  pkgconfig(wayland-scanner)
BuildRequires:  pkgconfig(wayland-server) >= 1.22.91
BuildRequires:  pkgconfig(xcursor)
BuildRequires:  pkgconfig(xkbcommon) >= 1.11.0

# XWayland support
BuildRequires:  pkgconfig(xcb)
BuildRequires:  pkgconfig(xcb-composite)
BuildRequires:  pkgconfig(xcb-errors)
BuildRequires:  pkgconfig(xcb-icccm)
BuildRequires:  pkgconfig(xcb-render)
BuildRequires:  pkgconfig(xcb-res)
BuildRequires:  pkgconfig(xcb-xfixes)

# Fedora 44 and older provide Lua 5.4. Fedora 45 and newer provide Lua 5.5
BuildRequires:  pkgconfig(lua) >= %{lua_min_version}
BuildRequires:  pkgconfig(lua) < %{lua_next_version}

# Fedora's udis86 package is a different implementation from the modified
# canihavesomecoffee fork bundled by Hyprland.
Provides:       bundled(udis86) = 1.7.2

# The stable and snapshot packages install the same paths.
Conflicts:      hyprland-git

Requires:       aquamarine%{?_isa} >= 0.15.0
Requires:       hyprcursor%{?_isa} >= 0.1.7
Requires:       hyprgraphics%{?_isa} >= 0.5.1
Requires:       hyprlang%{?_isa} >= 0.6.7
Requires:       hyprutils%{?_isa} >= 0.14.3
Requires:       xorg-x11-server-Xwayland%{?_isa}

# Used by the default configuration.
Recommends:     kitty
Recommends:     hyprland-guiutils
Recommends:     hyprlauncher

# Logind needs polkit to create a graphical session.
Recommends:     polkit

# Recommended systemd-managed startup method.
Recommends:     %{name}-uwsm

Recommends:     (qt5-qtwayland if qt5-qtbase-gui)
Recommends:     (qt6-qtwayland if qt6-qtbase-gui)

%description
Hyprland is a dynamic tiling Wayland compositor that doesn't sacrifice on its looks.
It supports multiple layouts, visual effects, flexible IPC, a powerful plugin system, and extensive customization.

%package uwsm
Summary:        Files for a uwsm-managed Hyprland session
BuildArch:      noarch
Requires:       %{name} = %{version}-%{release}
Requires:       uwsm
Conflicts:      hyprland-git-uwsm

%description uwsm
Desktop-session files for starting Hyprland through the Universal Wayland
Session Manager.

%package devel
Summary:        Development files for %{name}
License:        BSD-3-Clause
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig(xkbcommon) >= 1.11.0
Requires:       pkgconfig(lua) >= %{lua_min_version}
Requires:       pkgconfig(lua) < %{lua_next_version}
Requires:       pkgconfig(libeis-1.0)
Conflicts:      hyprland-git-devel

%description devel
Headers, generated protocol files, pkg-config metadata, and RPM macros for
building software and plugins against %{name}.

%prep
%autosetup -n hyprland-source -N
%patch -P 0 -p1

# Temporary compatibility for Fedora releases that still provide Lua 5.4
# Drop this branch once all supported Fedora releases provide Lua 5.5
%if %{lua54_compat}
%patch -P 1 -p1
sed -Ei \
    's|^pkg_search_module\(LUA .*$|pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua)|' \
    CMakeLists.txt

grep -Fxq \
    'pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua)' \
    CMakeLists.txt
%endif

# Export dependencies included by the installed Lua and input headers.
sed -i '/^Requires:/s/$/, lua >= %{lua_min_version}, lua < %{lua_next_version}, libeis-1.0/' hyprland.pc.in

cp -p subprojects/udis86/LICENSE LICENSE-udis86

# Generate the packaged RPM macro without modifying the source in %{_sourcedir}.
cp -p %{SOURCE1} macros.hyprland
sed -i 's|@@HYPRLAND_VERSION@@|%{version}|g' macros.hyprland

%build
export GIT_COMMIT_HASH=%{shescape:%{hyprland_commit}}
export GIT_TAG=%{shescape:%{upstream_version}}
export GIT_DIRTY=%{shescape:clean}

%cmake \
    -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON

%cmake_build

%install
%cmake_install

# Systemd user units belong in lib even when libraries are installed in lib64.
if [ "%{_libdir}/systemd/user" != "%{_userunitdir}" ]; then
    install -d %{buildroot}%{_userunitdir}
    mv %{buildroot}%{_libdir}/systemd/user/hyprland-session.target \
        %{buildroot}%{_userunitdir}/hyprland-session.target
fi

install -Dpm0644 macros.hyprland \
    %{buildroot}%{_rpmconfigdir}/macros.d/macros.hyprland

%files
%license LICENSE LICENSE-udis86
%{_bindir}/Hyprland
%{_bindir}/hyprland
%{_bindir}/hyprctl
%{_bindir}/hyprpm
%{_bindir}/start-hyprland
%{_datadir}/hypr/
%{_datadir}/wayland-sessions/hyprland.desktop
%{_datadir}/xdg-desktop-portal/hyprland-portals.conf
%{_userunitdir}/hyprland-session.target
%{_mandir}/man1/Hyprland.1*
%{_mandir}/man1/hyprctl.1*
%{bash_completions_dir}/hypr*
%{fish_completions_dir}/hypr*.fish
%{zsh_completions_dir}/_hypr*

%files uwsm
%{_datadir}/wayland-sessions/hyprland-uwsm.desktop

%files devel
%{_includedir}/hyprland/
%{_datadir}/pkgconfig/hyprland.pc
%{_rpmconfigdir}/macros.d/macros.hyprland

%changelog
%autochangelog
