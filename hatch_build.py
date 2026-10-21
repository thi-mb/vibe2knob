"""Bundle the built panel UI (ui/dist) into the wheel as vibe2knob/web, when it's built."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return  # an editable install serves ui/dist from the checkout
        dist = Path(self.root) / "ui" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "vibe2knob/web"
        else:
            self.app.display_warning("ui/dist isn't built; the wheel won't include the panel UI.")
