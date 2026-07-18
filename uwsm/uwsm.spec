Name:           uwsm
Version:        0.26.6
Release:        %autorelease
Summary:        Universal Wayland Session Manager

License:        MIT
URL:            https://github.com/Vladimir-csp/uwsm
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  desktop-file-utils
BuildRequires:  meson >= 1.3.0
BuildRequires:  pkgconfig(scdoc) >= 1.9.2
BuildRequires:  python3-devel
BuildRequires:  python3-dbus
BuildRequires:  python3-pyxdg
BuildRequires:  systemd-rpm-macros

Requires:       python3-dbus
Requires:       python3-pyxdg

# Optional at runtime; uwsm has reduced-functionality fallbacks without them.
Recommends:     /usr/bin/notify-send
Recommends:     /usr/bin/waitpid
Recommends:     /usr/bin/whiptail

%description
UWSM wraps standalone Wayland compositors in a set of systemd user units.
It provides environment setup and cleanup, XDG autostart integration,
bidirectional binding to the login session, application launching
in appropriate systemd slices and orderly session shutdown.

%prep
%autosetup -p1

%build
%meson \
    -Ddocdir=%{_pkgdocdir} \
    -Dlicensedir=%{_licensedir}/%{name} \
    -Dman-pages=enabled \
    -Dpublic-modules=disabled \
    -Duuctl=enabled \
    -Dfumon=enabled \
    -Dfumon-preset=enabled \
    -Duwsm-app=enabled
%meson_build

%install
%meson_install

# The Python modules are intentionally installed outside site-packages.
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}/modules

%check
desktop-file-validate \
    %{buildroot}%{_datadir}/applications/uuctl.desktop

%post
%systemd_user_post fumon.service

%preun
%systemd_user_preun fumon.service

%postun
%systemd_user_postun fumon.service

%files
%license %{_licensedir}/%{name}/LICENSE
%{_licensedir}/%{name}/depmf.json
%doc %{_pkgdocdir}/

%{_bindir}/%{name}
%{_bindir}/%{name}-app
%{_bindir}/%{name}-terminal
%{_bindir}/%{name}-terminal-scope
%{_bindir}/%{name}-terminal-service
%{_bindir}/fumon
%{_bindir}/uuctl

%{_datadir}/%{name}/
%{_datadir}/applications/uuctl.desktop
%{_libexecdir}/%{name}/

%{_mandir}/man1/%{name}.1.*
%{_mandir}/man1/%{name}-app.1.*
%{_mandir}/man3/%{name}-plugins.3.*
%{_mandir}/man1/fumon.1.*
%{_mandir}/man1/uuctl.1.*

%{_userunitdir}/*-graphical.slice
%{_userunitdir}/fumon.service
%{_userunitdir}/wayland-*.service
%{_userunitdir}/wayland-*.target
%{_userpresetdir}/80-fumon.preset

%changelog
%autochangelog