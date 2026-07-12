// Docs site for livekit-msteams-bridge (Python), published to GitHub Pages by .github/workflows/docs.yml.
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import mermaid from "astro-mermaid";

export default defineConfig({
  site: "https://komaa-com.github.io",
  base: "/livekit-msteams-bridge-py",
  integrations: [
    // Client-side Mermaid rendering (theme-aware, offline). Must come BEFORE starlight.
    mermaid({ theme: "default", autoTheme: true }),
    starlight({
      head: [
        // Google Analytics 4 (shared StandIn property; filter by hostname in GA).
        { tag: "script", attrs: { async: true, src: "https://www.googletagmanager.com/gtag/js?id=G-M02N9C42XH" } },
        {
          tag: "script",
          content:
            "window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','G-M02N9C42XH');",
        },
      ],
      title: "Microsoft Teams Bridge for LiveKit Agents (Python)",
      description:
        "Put a LiveKit Agent - including avatar agents - on a real Microsoft Teams call from Python: per-call rooms, explicit dispatch, 16 kHz PCM relay, data-topic context, and call governors, connected through the StandIn media bridge.",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/komaa-com/livekit-msteams-bridge-py",
        },
      ],
      sidebar: [
        { label: "Overview", link: "/" },
        { label: "Getting Started", link: "/getting-started/" },
        { label: "Run the Example", link: "/example/" },
        { label: "Connecting to StandIn", link: "/connecting-to-standin/" },
        { label: "Architecture", link: "/architecture/" },
        { label: "Agents and Dispatch", link: "/agents-and-dispatch/" },
        { label: "Configuration Reference", link: "/configuration-reference/" },
        { label: "Library API", link: "/library-api/" },
        { label: "Wire Protocol", link: "/wire-protocol/" },
        { label: "Governors and Privacy", link: "/governors-and-privacy/" },
        { label: "Troubleshooting", link: "/troubleshooting/" },
        { label: "Contributing", link: "/contributing/" },
      ],
    }),
  ],
});
