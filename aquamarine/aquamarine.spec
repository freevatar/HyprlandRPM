Name:           aquamarine
Version:        0.15.1
Release:        %autorelease -b2
Summary:        Lightweight Linux rendering backend library

# Aquamarine is BSD-3-Clause; generated Wayland protocol bindings are MIT.
License:        BSD-3-Clause AND MIT
URL:            https://github.com/hyprwm/aquamarine
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# All upstream commits after v0.15.1 through 163b138cfb22873de1e1bf253d57bf03154d1acd.
# drm: fix crash on null connector in flush
Patch0:         %{url}/commit/fc77bd58a2c9ef6d0760a7c9c3b7b6670f956f28.patch
# wayland: fix configure not applying sometimes
Patch1:         %{url}/commit/7bb8bdf4e8fedaf4dfae58512bc4c728671727df.patch
# drm: invalidate stale connector state in connect
Patch2:         %{url}/commit/082179bc5cf682175ee7aab41425eabc9e2305cf.patch
# drm: guard backend in log
Patch3:         %{url}/commit/04bfb7db7ef7d5b2cc6860623396c606ef015e96.patch
# build: link OpenGL::GLES3 instead of OpenGL::OpenGL on OpenBSD
Patch4:         %{url}/commit/52ea11f06dd492f34b3cc0b39a9c805beecafcff.patch
# allocator/dumb: drop fd if exported in destructor
Patch5:         %{url}/commit/be8909b544fb7c8def8be0b7624ecf3d99f3bfe5.patch
# headless: reuse parent Wayland buffer formats when nested
Patch6:         %{url}/commit/163b138cfb22873de1e1bf253d57bf03154d1acd.patch

# https://fedoraproject.org/wiki/Changes/EncourageI686LeafRemoval
ExcludeArch:    %{ix86}

BuildRequires:  cmake >= 3.19
BuildRequires:  gcc-c++
BuildRequires:  mesa-libEGL-devel
BuildRequires:  pkgconfig(gbm)
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
%ctest --output-on-failure -R '^(attachments|output|commitThread)$'

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
