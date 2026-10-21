# Modernization and release roadmap

| Stage | Status and result |
|---|---|
| 1: installation and launch | Complete: installable uv package, windowed launcher, native icon, resource paths and console workers |
| 2: dependency readiness | Complete: cancellable System check, saved video paths, optional SDK/wheel installation and external dependency guides |
| 3: shared appearance | Complete: packaged reference colors, fonts, icons and Light/Dark/System themes |
| 4: navigation | Complete: persistent sidebar/pages, experiment header, responsive controls and retained drafts/resources |
| 5: performance and cleanup | Complete in 3.2.5: restored layout panning, bounded message display, cached previews, batched routing and consolidated project structure |
| 6: first public release preparation | Authorized: v1.0.0 metadata, repository cleanup, distribution checks, CI and publication guides |

The intended repository is **Marcin-A-Lipiec/deepecohab-acquisition**. Version
**1.0.0** marks the first public release after the internal 3.2.x modernization
series. Follow [RELEASING.md](RELEASING.md) for the curated source archive,
package validation and GitHub upload steps. The owner selected MIT for the
application's original code; package metadata and documentation now agree with
the existing LICENSE. The local preparation tools do not publish tags or releases.

Final physical acceptance includes a fresh-PC uv installation, visible Explorer double-click,
update/rollback/removal with retained experiment data, and the exact camera SDK
and vendor wheel used on the acquisition PC. Run simultaneous RFID, camera,
trigger and lighting acquisition; check TTL inputs, disconnect/reconnect,
stop/drain/close, sustained recording and video decode/frame accounting.
Read representative exports with the selected downstream analysis release.

The existing Desktop launcher has been checked by the user. Automated installed
checks also exercise the native Windows shortcut, but they do not replace the
simultaneous-device tests above. Complete and record those checks on the chosen
acquisition PC before describing the release as physically validated.

The supplied duplicate-data example showed one startup timestamp collision.
The owner has chosen to monitor for further evidence rather than treat it as an
active bug. No stitching/interpolation/timestamp logic is changed for publication.

For future features, follow [architecture](../ARCHITECTURE.md) and
[testing](../TESTING.md). Add a controller/protocol/service boundary for new
hardware, register new pages by stable key, and version scientific contracts
only when their public data meaning changes. Preserve meaningful regression
fixtures and use measured improvements before altering acquisition hot paths.
