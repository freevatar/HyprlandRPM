Name:           aquamarine
Version:        0.14.0
Release:        %autorelease
Summary:        Lightweight Linux rendering backend library

License:        BSD-3-Clause
URL:            https://github.com/hyprwm/aquamarine
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

Patch:          https://github.com/hyprwm/aquamarine/commit/5ea27f81a3dac099e95b220690dc675de6d8fbd9.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/fc39af0ccec9171aec8185eaea8afb71a46eff90.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/cf454160f2e9432263e2a2a531ce546a07033d01.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/5a9a6da9dd31efd3c4141abf034c74a5e00a0fd9.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/1a10fe26a9f7d989c359e6a9ea61aa2e44d06c36.patch

# https://fedoraproject.org/wiki/Changes/EncourageI686LeafRemoval
ExcludeArch:    %{ix86}

BuildRequires:  cmake >= 3.19
BuildRequires:  gcc-c++
BuildRequires:  mesa-libEGL-devel
BuildRequires:  pkgconfig(gbm)
BuildRequires:  pkgconfig(hwdata)
BuildRequires:  pkgconfig(hyprutils) >= 0.8.0
BuildRequires:  pkgconfig(hyprwayland-scanner) >= 0.4.0
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
Requires:       pkgconfig(hyprutils) >= 0.8.0
Requires:       pkgconfig(libdrm)

%description devel
The %{name}-devel package contains the headers, shared-library linker name,
and pkg-config metadata needed to develop applications using Aquamarine.

%prep
%autosetup -p1

%build
%cmake -DCMAKE_BUILD_TYPE=Release
%cmake_build

%install
%cmake_install

%check
# simpleWindow needs a usable Wayland session; attachments is headless.
%ctest --output-on-failure -R '^attachments$'

%files
%license LICENSE
%doc README.md
%{_libdir}/lib%{name}.so.*

%files devel
%{_includedir}/%{name}/
%{_libdir}/lib%{name}.so
%{_libdir}/pkgconfig/%{name}.pc

%changelog
%autochangelog
