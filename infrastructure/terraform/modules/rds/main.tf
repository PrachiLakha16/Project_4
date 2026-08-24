# Single Postgres instance used for BOTH the LangGraph checkpointer state
# AND the pgvector-enabled runbook_chunks table used for RAG over runbooks.
# See the repo's ADR: "Postgres over Redis for the checkpointer, pgvector
# over a standalone vector DB" — one managed instance instead of two
# separate stateful systems to operate.

resource "random_password" "master" {
  length      = 32
  special     = true
  # RDS master password does not accept "/", "@", '"', or " ".
  override_special = "!#$%^&*()-_=+[]{}<>:?"
}

resource "aws_db_subnet_group" "this" {
  name       = "sre-incident-agent-${var.environment}"
  subnet_ids = var.subnet_ids

  tags = merge(var.tags, {
    Name = "sre-incident-agent-${var.environment}"
  })
}

resource "aws_security_group" "rds" {
  name        = "sre-incident-agent-${var.environment}-rds"
  description = "Allow Postgres ingress from the sre-incident-agent EKS workload only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name = "sre-incident-agent-${var.environment}-rds"
  })
}

resource "aws_security_group_rule" "ingress_from_eks" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = var.eks_node_security_group_id
  description               = "Postgres from sre-incident-agent pods/nodes only"
}

resource "aws_security_group_rule" "egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  security_group_id = aws_security_group.rds.id
  cidr_blocks       = ["0.0.0.0/0"]
  description        = "Default egress (RDS itself does not need to reach out beyond AWS-managed replication/backup, but SGs require an explicit rule)"
}

resource "aws_db_parameter_group" "this" {
  name   = "sre-incident-agent-${var.environment}-pg"
  family = "postgres16"

  # pgvector ships with modern Postgres engine versions on RDS as an
  # allow-listed extension; it still must be created explicitly per-database
  # via `CREATE EXTENSION vector;` (done by the app's migration tooling, not
  # Terraform) but shared_preload_libraries is not required for pgvector.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  tags = var.tags
}

resource "aws_db_instance" "this" {
  identifier     = "sre-incident-agent-${var.environment}"
  engine         = "postgres"
  engine_version = var.engine_version

  instance_class        = var.instance_class
  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.master_username
  password = random_password.master.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.this.name

  multi_az            = var.multi_az
  publicly_accessible = false

  backup_retention_period = var.backup_retention_days
  backup_window            = "03:00-04:00"
  maintenance_window       = "mon:04:30-mon:05:30"

  deletion_protection      = var.deletion_protection
  skip_final_snapshot      = false
  final_snapshot_identifier = "sre-incident-agent-${var.environment}-final"

  copy_tags_to_snapshot = true
  auto_minor_version_upgrade = true

  tags = merge(var.tags, {
    Name = "sre-incident-agent-${var.environment}"
  })
}
