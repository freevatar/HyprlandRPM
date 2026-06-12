%global upstream_version 0.55.4

Name:           hyprland
Version:        %{upstream_version}
Release:        %autorelease -b3
Summary:        Dynamic tiling Wayland compositor that doesn't sacrifice on its looks

# hyprland: BSD-3-Clause
# subprojects/hyprland-protocols: BSD-3-Clause
# subproject/udis86: BSD-2-Clause
# protocols/ext-workspace-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-foreign-toplevel-management-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-layer-shell-unstable-v1.xml: HPND-sell-variant
# protocols/idle.xml: LGPL-2.1-or-later
License:        BSD-3-Clause AND BSD-2-Clause AND HPND-sell-variant AND LGPL-2.1-or-later
URL:            https://github.com/hyprwm/Hyprland
Source0:        %{url}/releases/download/v%{upstream_version}/source-v%{upstream_version}.tar.gz
Source4:        macros.hyprland

%{lua:
hyprdeps = {
    "cmake >= 3.30",
    "gcc-c++",
    "glaze-static",
    "meson",
    "muParser-devel",
    "pkgconfig(aquamarine) >= 0.9.3",
    "pkgconfig(cairo)",
    "pkgconfig(egl)",
    "pkgconfig(gbm)",
    "pkgconfig(gio-2.0)",
    "pkgconfig(glesv2)",
    "pkgconfig(glslang)",
    "pkgconfig(hwdata)",
    "pkgconfig(hyprcursor) >= 0.1.7",
    "pkgconfig(hyprgraphics) >= 0.5.1",
    "pkgconfig(hyprlang) >= 0.6.7",
    "pkgconfig(hyprutils) >= 0.13.1",
    "pkgconfig(hyprwayland-scanner) >= 0.3.10",
    "pkgconfig(hyprwire)",
    "pkgconfig(lcms2)",
    "pkgconfig(libdisplay-info)",
    "pkgconfig(libdrm)",
    "pkgconfig(libinput) >= 1.28",
    "pkgconfig(libliftoff)",
    "pkgconfig(libseat)",
    "pkgconfig(libudev)",
    "pkgconfig(lua)",
    "pkgconfig(pango)",
    "pkgconfig(pangocairo)",
    "pkgconfig(pixman-1)",
    "pkgconfig(re2)",
    "pkgconfig(systemd)",
    "pkgconfig(tomlplusplus)",
    "pkgconfig(uuid)",
    "pkgconfig(wayland-client)",
    "pkgconfig(wayland-protocols) >= 1.47",
    "pkgconfig(wayland-scanner)",
    "pkgconfig(wayland-server) >= 1.22.91",
    "pkgconfig(xcb-composite)",
    "pkgconfig(xcb-dri3)",
    "pkgconfig(xcb-errors)",
    "pkgconfig(xcb-ewmh)",
    "pkgconfig(xcb-icccm)",
    "pkgconfig(xcb-present)",
    "pkgconfig(xcb-render)",
    "pkgconfig(xcb-renderutil)",
    "pkgconfig(xcb-res)",
    "pkgconfig(xcb-shm)",
    "pkgconfig(xcb-util)",
    "pkgconfig(xcb-xfixes)",
    "pkgconfig(xcb-xinput)",
    "pkgconfig(xcb)",
    "pkgconfig(xcursor)",
    "pkgconfig(xkbcommon) >= 1.11.0",
    "pkgconfig(xwayland)"
    }
}

%define printbdeps(r) %{lua:
for _, dep in ipairs(hyprdeps) do
    print((rpm.expand("%{-r}") ~= "" and "Requires: " or "BuildRequires: ")..dep.."\\n")
end
}

%printbdeps
BuildRequires:  python3

# udis86 is packaged in Fedora, but the copy bundled here is actually a
# modified fork.
Provides:       bundled(udis86) = 1.7.2

Requires:       xorg-x11-server-Xwayland%{?_isa}
Requires:       aquamarine%{?_isa} >= 0.9.3
Requires:       hyprcursor%{?_isa} >= 0.1.7
Requires:       hyprgraphics%{?_isa} >= 0.5.1
Requires:       hyprlang%{?_isa} >= 0.6.7
Requires:       hyprutils%{?_isa} >= 0.13.1

# Used in the default configuration
Recommends:     kitty
Recommends:     hyprland-qtutils
# Logind needs polkit to create a graphical session
Recommends:     polkit
# https://wiki.hyprland.org/Useful-Utilities/Systemd-start
Recommends:     %{name}-uwsm
Recommends:     (qt5-qtwayland if qt5-qtbase-gui)
Recommends:     (qt6-qtwayland if qt6-qtbase-gui)

%description
Hyprland is a dynamic tiling Wayland compositor that doesn't sacrifice
on its looks. It supports multiple layouts, fancy effects, has a
very flexible IPC model allowing for a lot of customization, a powerful
plugin system and more.

%package        uwsm
Summary:        Files for a uwsm-managed session
Requires:       uwsm
%description    uwsm
Files for a uwsm-managed session.

%package        devel
Summary:        Header and protocol files for %{name}
License:        BSD-3-Clause
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig(xkbcommon) >= 1.11.0

%description    devel
%{summary}.


%prep
%autosetup -n hyprland-source -N

# Temporary workaround
# Fedora 43 still ships Lua 5.4
# Upstream Hyprland requests Lua 5.5
# Drop this once building against Lua 5.5 is available
sed -i -e '/pkg_check_modules(/,/)/s|\<lua55\>|lua|g' CMakeLists.txt

cp -p subprojects/hyprland-protocols/LICENSE LICENSE-hyprland-protocols
cp -p subprojects/udis86/LICENSE LICENSE-udis86

sed -i \
  -e "s|@@HYPRLAND_VERSION@@|%{version}|g" \
  %{SOURCE4}


%build
export GIT_TAG=%{upstream_version}
export GIT_DIRTY=clean

%cmake \
    -GNinja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=FALSE
%cmake_build


%install
%cmake_install
install -Dpm644 %{SOURCE4} -t %{buildroot}%{_rpmconfigdir}/macros.d


%files
%license LICENSE LICENSE-udis86 LICENSE-hyprland-protocols
%{_bindir}/Hyprland
%{_bindir}/hyprland
%{_bindir}/hyprctl
%{_bindir}/hyprpm
%{_bindir}/start-hyprland
%{_datadir}/hypr/
%{_datadir}/wayland-sessions/hyprland.desktop
%{_datadir}/xdg-desktop-portal/hyprland-portals.conf
%{_mandir}/man1/hyprctl.1*
%{_mandir}/man1/Hyprland.1*
%{bash_completions_dir}/hypr*
%{fish_completions_dir}/hypr*.fish
%{zsh_completions_dir}/_hypr*

%files uwsm
%{_datadir}/wayland-sessions/hyprland-uwsm.desktop

%files devel
%{_datadir}/pkgconfig/hyprland.pc
%{_includedir}/hyprland/
%{_rpmconfigdir}/macros.d/macros.hyprland


%changelog
%autochangelog
