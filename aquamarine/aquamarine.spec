Name:           aquamarine
Version:        0.16.0
Release:        %autorelease -b5
Summary:        Lightweight Linux rendering backend library

# Aquamarine is BSD-3-Clause; generated Wayland protocol bindings are MIT.
License:        BSD-3-Clause AND MIT
URL:            https://github.com/hyprwm/aquamarine
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

# All upstream commits after v0.16.0 through 8126552b0d3c1717f92867772ec573d3d2ae8a20.
# internal: fix multi-GPU buffer fallbacks and invalid modifier comparisons
Patch0:         %{url}/commit/ad4484335fc327a6cf010f455ae258c397861ee0.patch
# drm: synchronously recheck CRTCs on connector enable state change
Patch1:         %{url}/commit/42c396067a5a59491a45138528d49238dd6e394e.patch
# DRM: fix typo in EDID chromaticity log
Patch2:         %{url}/commit/612609446794933ba138bb7f86cb6de2ea4a396f.patch
# drm: avoid invalid all-zero HDR metadata for SDR displays
Patch3:         %{url}/commit/801f25ffc581ef6796e9b0721264d50ebfdb23b1.patch
# gbm: fix buffer allocation, modifier handling, and renderer state caching
Patch4:         %{url}/commit/a5cdfb3f53f3b9422cff7548649e83cbeae7cb37.patch
# DRM: allow output tests while an asynchronous commit is pending
Patch5:         %{url}/commit/9887f39574f2afeae3aeb6687db6cd0c030ccf94.patch
# nix: propagate dependencies used by public headers
Patch6:         %{url}/commit/a43be73731601deb649a2dc94bef5a60100d132e.patch
# nix: drop the redundant Pixman dependency
Patch7:         %{url}/commit/7c1c55b2a76ddb9f6602f8c0886ecf1b899529f9.patch
# renderer/egl: avoid making the same context current twice
Patch8:         %{url}/commit/e36d13c12982ec51f15ef4a6e3d10efbc25e7078.patch
# drm/atomic: preserve the output format across asynchronous commits
Patch9:         %{url}/commit/7099ca2eeda9851841822f4a101786b0ac9856a5.patch
# drm/edid: report HLG support
Patch10:        %{url}/commit/90cd015f5abc1863402d25659df4b061fbaadccf.patch
# renderer/sync: wait on acquire fences in the readback context
Patch11:        %{url}/commit/d542cb8a241a138e8166d525aff1308657e75b03.patch
# renderer/sync: release the final blit fence during teardown
Patch12:        %{url}/commit/96eb66553cbde7c8330b33024376c392af3e9bb9.patch
# drm: skip duplicate plane format modifiers
Patch13:        %{url}/commit/d2aca076f2b1938347bb42ed972b01e3e24ce5d3.patch
# wayland: preserve swapchain allocation options on commit
Patch14:        %{url}/commit/3bd9ae51579d9c10cfc5a67342444686bb021068.patch
# docs: add upstream AGENTS.md
Patch15:        %{url}/commit/2e77bc4c96bf45bcc5b167dd5da5556ed6b882ce.patch
# backend: drop the session when nested without DRM
Patch16:        %{url}/commit/8126552b0d3c1717f92867772ec573d3d2ae8a20.patch

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
