# v2 slice 5 (PRD FR13): the generation VM's shape and every cost bound live here, reviewed in a plan; the lifecycle belongs to the
# launcher (scripts/launch_generation.py), which creates one instance per run from this template, passes the startup script
# and the run's metadata, deletes the VM and proves nothing labelled remains. A VM resource was rejected: a self-deleted,
# preempted or timed-out VM would leave state drift, and an unrelated apply would recreate it and start another generation.
# One template per run class, named with its duration, so slice 6 adds the `main` class without touching `pilot`. No
# create_before_destroy: every template attribute forces replacement, and a same-name replacement created first would fail
# with alreadyExists; nothing depends on the old template staying up (instances created from it are standalone). No startup script here: an instance created from the template by hand does
# nothing, and editing the script never replaces the template. Never the default compute service account.

resource "google_compute_instance_template" "synthea_generator" {
  for_each = var.generation_templates

  name         = "synthea-generator-${each.key}-${each.value}s"
  machine_type = "n2-standard-32"
  description  = "Synthea generation VM (${each.key}): spot, deleted at ${each.value}s or on preemption, no external IP"

  scheduling {
    provisioning_model          = "SPOT"
    preemptible                 = true
    automatic_restart           = false
    on_host_maintenance         = "TERMINATE"
    instance_termination_action = "DELETE"
    max_run_duration {
      seconds = each.value
    }
  }

  disk {
    source_image = "debian-cloud/debian-13"
    disk_type    = "pd-balanced"
    disk_size_gb = 200
    auto_delete  = true
    boot         = true
    labels = {
      purpose = "synthea-generation"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.spark_us_central1.id
  }

  service_account {
    email  = google_service_account.synthea_generator.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "true"
  }

  labels = {
    purpose = "synthea-generation"
  }


  depends_on = [google_project_service.enabled]
}
