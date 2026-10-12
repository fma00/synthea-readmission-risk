# v2 slice 5: a dedicated network for the generation VM and the Dataproc Serverless batch. The default network's us-central1
# subnet has Private Google Access off (checked 2026-10-12); Dataproc Serverless needs it, and the VM reaches GCS through it.
# No external IPs anywhere and no other ingress: nothing here needs the internet (the JDK comes from the artifacts bucket).

resource "google_compute_network" "spark" {
  name                    = "spark"
  auto_create_subnetworks = false

  depends_on = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "spark_us_central1" {
  name                     = "spark-us-central1"
  region                   = var.region
  network                  = google_compute_network.spark.id
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  depends_on = [google_project_service.enabled]
}

# Dataproc Serverless requires "internal communication on all ports between VM instances within the subnet".
resource "google_compute_firewall" "spark_internal" {
  name          = "spark-allow-internal"
  network       = google_compute_network.spark.name
  direction     = "INGRESS"
  source_ranges = [google_compute_subnetwork.spark_us_central1.ip_cidr_range]

  allow {
    protocol = "all"
  }

  depends_on = [google_project_service.enabled]
}
