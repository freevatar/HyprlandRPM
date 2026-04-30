Name:           hyprland-protocols
Version:        0.7.0
Release:        %autorelease -b3
Summary:        Wayland protocol extensions for Hyprland

BuildArch:      noarch
License:        BSD-3-Clause
URL:            https://github.com/hyprwm/hyprland-protocols
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# ToDo: Remove after next upstream release
Patch0:          https://github.com/hyprwm/hyprland-protocols/commit/3f3860b869014c00e8b9e0528c7b4ddc335c21ab.patch

BuildRequires:  cmake >= 3.20

%description
%{summary}.

%package        devel
Summary:        Wayland protocol extensions for Hyprland

%description    devel
%{summary}.

%prep
%autosetup -p1 -N

# ToDo: Remove after next upstream release
%autopatch -p1


%build
%cmake
%cmake_build

%install
%cmake_install

%files devel
%license LICENSE
%doc README.md
%{_datadir}/pkgconfig/%{name}.pc
%{_datadir}/%{name}/

%changelog
%autochangelog
