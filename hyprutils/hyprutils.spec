Name:           hyprutils
Version:        0.14.0
Release:        %autorelease
Summary:        Hyprland utilities library used across the ecosystem

License:        BSD-3-Clause
URL:            https://github.com/hyprwm/%{name}
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# https://fedoraproject.org/wiki/Changes/EncourageI686LeafRemoval
ExcludeArch:    %{ix86}

# CMake 3.25 added support for CXX_STANDARD 26, which upstream requests.
BuildRequires:  cmake >= 3.25
BuildRequires:  gcc-c++
BuildRequires:  pkgconfig(pixman-1)

%description
hyprutils is a small C++ library providing utility types and helper functionality.

%package devel
Summary:        Development files for %{name}
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig(pixman-1)

%description devel
The %{name}-devel package contains the headers, unversioned shared-library
link and pkg-config metadata needed to develop software using %{name}.

%prep
%autosetup

# Honor distribution compiler optimization flags instead of forcing -O3.
sed -i '/^[[:space:]]*add_compile_options(-O3)[[:space:]]*$/d' CMakeLists.txt

# Region.hpp publicly includes pixman.h and exposes pixman types.
# Propagate Pixman's compiler flags to consumers of the hyprutils pkg-config module.
sed -i '/^Version:/a Requires: pixman-1' hyprutils.pc.in

%build
%cmake
%cmake_build

%install
%cmake_install

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