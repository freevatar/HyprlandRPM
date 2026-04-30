Name:           hyprland-protocols
Version:        0.7.0
Release:        %autorelease -b2
Summary:        Wayland protocol extensions for Hyprland

BuildArch:      noarch
License:        BSD-3-Clause
URL:            https://github.com/hyprwm/hyprland-protocols
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildRequires:  cmake >= 3.20

%description
%{summary}.

%package        devel
Summary:        Wayland protocol extensions for Hyprland

%description    devel
%{summary}.

%prep
%autosetup -p1

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
