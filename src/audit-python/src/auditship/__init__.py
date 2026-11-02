"""auditship - ship Linux auditd records to Loki on the trainer (the Linux counterpart of etwcollect's shipper).

    kernel audit rules (linux_auditd role: exec, net, file watches; the shipper's own uid excluded)
      -> auditd writes /var/log/audit/audit.log (one record per line, ENRICHED format)
      -> auditship tails it from a saved {inode, offset}, follows rotation
      -> Loki push API {job="auditd", host}, entry time = the record's own audit(...) time

Module map: README.md.
"""
__version__ = "0.1.0"
