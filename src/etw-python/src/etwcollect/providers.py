"""providers.py - the three kernel ETW providers the lab records, by name.

etwlab.json (written by Ansible, src/ansible/roles/win_collector/templates/etwlab.json.j2) names providers
and keywords; ETW needs the provider GUID. The GUIDs are the ones SilkETW wrote into every line's
`ProviderGuid` (checked on src/live/sessions/live-20261025T073042Z/raw/*.json).
"""
import uuid

GUIDS: dict[str, uuid.UUID] = {
    "Microsoft-Windows-Kernel-Process": uuid.UUID("22fb2cd6-0e7b-422b-a0c7-2fad1fd0e716"),
    "Microsoft-Windows-Kernel-File":    uuid.UUID("edd08927-9cc4-4e65-b970-c2560fb5c289"),
    "Microsoft-Windows-Kernel-Network": uuid.UUID("7dd42a49-5329-4832-8dfd-43d979153a88"),
}

NETWORK = "Microsoft-Windows-Kernel-Network"


class Provider:
    """One collector entry of etwlab.json: {"Stem": "kfile", "Provider": "...Kernel-File", "Keywords": "0x380"}."""

    def __init__(self, stem: str, name: str, keywords: int | str, required: bool = True):
        self.stem = stem                      # file stem and Loki `provider` label: kproc | kfile | knet
        self.name = name
        self.keywords = int(keywords, 16) if isinstance(keywords, str) else int(keywords)
        self.required = required
        if name not in GUIDS:
            raise ValueError(f"unknown provider {name!r} (known: {', '.join(GUIDS)})")
        self.guid: uuid.UUID = GUIDS[name]
        # EVENT_HEADER.ProviderId is a Windows GUID struct: the first three fields are little-endian, which
        # is exactly uuid.bytes_le. bytes(header.ProviderId) == self.guid_le identifies the provider per event.
        self.guid_le: bytes = self.guid.bytes_le

    @classmethod
    def from_config(cls, c: dict) -> "Provider":
        return cls(c["Stem"], c["Provider"], c.get("Keywords", "0xffffffffffffffff"), c.get("Required", True))
