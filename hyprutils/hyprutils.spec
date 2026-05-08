Name:           hyprutils
Version:        0.13.0
Release:        %autorelease -b2
Summary:        Hyprland utilities library used across the ecosystem

License:        BSD-3-Clause
URL:            https://github.com/hyprwm/hyprutils
Source0:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# ToDo: Remove after next upstream release
Patch0:         https://github.com/hyprwm/hyprutils/commit/3e170e5ad010602671f5f25b327e8bdb8fdd532c.patch

# https://fedoraproject.org/wiki/Changes/EncourageI686LeafRemoval
ExcludeArch:    %{ix86}

BuildRequires:  cmake >= 3.19
BuildRequires:  gcc-c++
BuildRequires:  pkgconfig(pixman-1)

%description
%{summary}.

%package        devel
Summary:        Development files for %{name}
Requires:       %{name}%{?_isa} = %{version}-%{release}

%description    devel
Development files for %{name}.

%prep
%autosetup -p1 -N

# ToDo: Remove after next upstream release
%autopatch -p1

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
