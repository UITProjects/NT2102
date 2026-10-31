"""etwcollect - the lab's ETW collector (replaces SilkETW/SilkService + Grafana Alloy, DEVIATIONS D10).

    ETW real-time session (Kernel-Process, Kernel-File, Kernel-Network)
      -> callback: exclude our own I/O, decode the payload (TDH schema, cached), one SilkETW-format JSON line
      -> raw_root\\<split>\\<run>\\{kproc,kfile,knet}.json   (batched: one write per file per second)
      -> shipper thread: tail those files from saved offsets -> Loki push API (same labels Alloy used)

Module map: README.md.
"""
__version__ = "0.1.0"
