Name:           aquamarine
Version:        0.12.1
Release:        %autorelease -b2
Summary:        Lightweight Linux rendering backend library

License:        BSD-3-Clause
URL:            https://github.com/hyprwm/aquamarine
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# All upstream commits after v0.12.1
Patch:          https://github.com/hyprwm/aquamarine/commit/c5fc63054547b89d0a29c51581ff19c554b57551.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/6333f8fe987ecc86b0ecdf866d432e79fcccfc32.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/d77e783ace2def8c509fea053b5110ad6699a7ef.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/107c83e95befca06850f6f1e8f7c3f7440ba0076.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/c49d9c4ade35561ed420e6f19ed1c1f1b3ab0ea5.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/6d6e2384f381def4ea4ea81543cba4bbdac72457.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/6ff6996a86bc5628b089cdc76e79cdbd2633a6cb.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/e68b8002b3c17478274d5e57002e460ccaf164a8.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/726a228aa66ee39373d92093b1e2a07af6aeeb1c.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/063d888352d507a61c9dc305b3f61ec90bad3c69.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/86f26251004355ef0d7a7ecf3b57e21c5471c05b.patch
Patch:          https://github.com/hyprwm/aquamarine/commit/817d9859bd57f9bbf5351147d1c3f6763d66089f.patch

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
