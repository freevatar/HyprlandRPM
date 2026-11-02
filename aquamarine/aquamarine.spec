Name:           aquamarine
Version:        0.16.0
Release:        %autorelease
Summary:        Lightweight Linux rendering backend library

# Aquamarine is BSD-3-Clause; generated Wayland protocol bindings are MIT.
License:        BSD-3-Clause AND MIT
URL:            https://github.com/hyprwm/aquamarine
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# All upstream commits after v0.16.0 through ad4484335fc327a6cf010f455ae258c397861ee0.
# internal: fix multi-GPU buffer fallbacks and invalid modifier comparisons
Patch0:         %{url}/commit/ad4484335fc327a6cf010f455ae258c397861ee0.patch

# Keep forced connector queries outside the compositor's realtime CPU budget.
# https://github.com/freevatar/aquamarine/commit/87c985ed4e04221b7766dcd1b1843b0094915689
Patch1:         nonrt-connector-probes.patch

# https://fedoraproject.org/wiki/Changes/EncourageI686LeafRemoval
ExcludeArch:    %{ix86}

BuildRequires:  cmake >= 3.27
BuildRequires:  gcc-c++
BuildRequires:  mesa-libEGL-devel
BuildRequires:  pkgconfig(gbm)
BuildRequires:  pkgconfig(glesv2)
BuildRequires:  pkgconfig(hwdata)
# The public backend API uses CLoggerConnection, added in hyprutils 0.10.3.
BuildRequires:  pkgconfig(hyprutils) >= 0.10.3
# Older scanners generate empty arrays rejected by current C++ compilers.
BuildRequires:  pkgconfig(hyprwayland-scanner) >= 0.4.5
BuildRequires:  pkgconfig(libdisplay-info)
BuildRequires:  pkgconfig(libdrm)
BuildRequires:  pkgconfig(libinput) >= 1.26.0
BuildRequires:  pkgconfig(libseat) >= 0.8.0
BuildRequires:  pkgconfig(libudev)
BuildRequires:  pkgconfig(pixman-1)
BuildRequires:  pkgconfig(wayland-client)
BuildRequires:  pkgconfig(wayland-protocols)
BuildRequires:  pkgconfig(wayland-scanner)

%description
Aquamarine is a lightweight Linux rendering backend library. It provides
basic abstractions for applications rendering in a Wayland window or directly
through a native DRM session. It is rendering-API agnostic and designed to be
lightweight, performant and minimal.

%package devel
Summary:        Development files for %{name}
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig(hyprutils) >= 0.10.3
Requires:       pkgconfig(libdrm)
Requires:       pkgconfig(wayland-client)

%description devel
The %{name}-devel package contains the headers, shared-library linker name,
and pkg-config metadata needed to develop applications using Aquamarine.

%prep
%autosetup -p1

# Export compiler and linker flags required by the installed public headers.
sed -i '/^Version:/a Requires: hyprutils >= 0.10.3, libdrm, wayland-client' aquamarine.pc.in

%build
%cmake -DCMAKE_BUILD_TYPE=Release
%cmake_build

%install
%cmake_install

# Wayland.hpp includes these generated headers; keep them in Aquamarine's namespace.
install -Dpm0644 -t %{buildroot}%{_includedir}/%{name}/protocols protocols/*.hpp
sed -i -E 's@^#include <(wayland|xdg-shell|linux-dmabuf-v1)\.hpp>@#include <aquamarine/protocols/\1.hpp>@' \
    %{buildroot}%{_includedir}/%{name}/backend/Wayland.hpp \
    %{buildroot}%{_includedir}/%{name}/protocols/*.hpp
sed -n '/^\/\*$/,/^\*\/$/p' protocols/*.hpp > LICENSE-protocols

%check
# simpleWindow needs a usable Wayland session; the remaining tests are headless.
%ctest --output-on-failure -R '^(attachments|output|commitThread|connectorProbe|connectorProbeRT)$'

%files
%license LICENSE LICENSE-protocols
%doc README.md
%{_libdir}/lib%{name}.so.*

%files devel
%{_includedir}/%{name}/
%{_libdir}/lib%{name}.so
%{_libdir}/pkgconfig/%{name}.pc

%changelog
%autochangelog
