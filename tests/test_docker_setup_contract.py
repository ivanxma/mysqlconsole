import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DOCKER_DIR = ROOT_DIR / "docker"


class DockerSetupContractTests(unittest.TestCase):
    def test_compose_shares_the_mysql_socket_only_with_dbconsole(self):
        compose = (DOCKER_DIR / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("context: ..", compose)
        self.assertIn("dockerfile: docker/Dockerfile", compose)
        self.assertIn("DBCONSOLE_LOCAL_MYSQL_SOCKET: /var/run/mysqld/mysqld.sock", compose)
        self.assertIn("DBCONSOLE_DEPLOYMENT_MODE: docker", compose)
        self.assertIn("DBCONSOLE_OBJECT_STORAGE_AUTH_MODE: oci_config_then_instance_principal", compose)
        self.assertEqual(compose.count("- dbconsole-mysql-socket:/var/run/mysqld"), 2)
        self.assertIn("dbconsole-mysql-socket:", compose)
        self.assertIn('command: ["mysqld", "--skip-networking", "--skip-mysqlx"]', compose)
        self.assertIn('MYSQL_ROOT_PASSWORD: ${DBCONSOLE_LOCAL_ADMIN_PASSWORD:-ChangeMe123!}', compose)
        self.assertNotIn("MYSQL_ROOT_HOST", compose)
        self.assertNotIn("3307:3306", compose)

    def test_entrypoint_creates_a_socket_backed_local_admin_profile(self):
        entrypoint = (DOCKER_DIR / "docker-entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('"socket_enabled": True', entrypoint)
        self.assertIn('"username": "localadmin"', entrypoint)
        self.assertIn('"require_password_change": True', entrypoint)
        self.assertIn('reconciled_profile["require_password_change"] = bool(item.get("require_password_change"))', entrypoint)
        self.assertIn('"socket_path": os.environ.get("DBCONSOLE_LOCAL_MYSQL_SOCKET", "/var/run/mysqld/mysqld.sock")', entrypoint)

    def test_setup_script_verifies_the_socket_profile_after_recreation(self):
        setup_script = (DOCKER_DIR / "setup_docker.sh").read_text(encoding="utf-8")

        self.assertIn("compose pull mysql", setup_script)
        self.assertIn("socket_path.is_socket()", setup_script)
        self.assertIn("The socket-backed local-admin-profile was not created.", setup_script)
        self.assertIn("DROP USER IF EXISTS", setup_script)
        self.assertIn("DBCONSOLE_LOCAL_ADMIN_PASSWORD=ChangeMe123!", setup_script)
        self.assertIn("-ulocaladmin", setup_script)
        self.assertIn("SELECT @@skip_networking", setup_script)
        self.assertIn(":0CEA$", setup_script)
        self.assertIn(":8114$", setup_script)

    def test_deployment_assets_are_grouped_by_target(self):
        for path in (
            DOCKER_DIR / "Dockerfile",
            DOCKER_DIR / "compose.yaml",
            DOCKER_DIR / "README.md",
            DOCKER_DIR / "setup_docker.sh",
            ROOT_DIR / "OL9" / "README.md",
            ROOT_DIR / "OL9" / "setup.sh",
            ROOT_DIR / "OL9" / "oci_compute_init.sh",
            ROOT_DIR / "OL9" / "init_docker.sh",
            ROOT_DIR / "ubuntu" / "README.md",
            ROOT_DIR / "ubuntu" / "setup.sh",
            ROOT_DIR / "ubuntu" / "oci_compute_init.sh",
        ):
            self.assertTrue(path.is_file(), path)

    def test_docker_image_includes_mysql_shell_for_dump_and_load_jobs(self):
        dockerfile = (DOCKER_DIR / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("MYSQL_SHELL_VERSION", dockerfile)
        self.assertIn("mysql-shell-${MYSQL_SHELL_VERSION}-linux-glibc2.28", dockerfile)
        self.assertIn("/usr/local/bin/mysqlsh", dockerfile)
        self.assertIn("/etc/pki/tls/certs/ca-bundle.crt", dockerfile)

    def test_each_target_readme_points_to_its_own_setup_entry_point(self):
        docker_readme = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
        ol9_readme = (ROOT_DIR / "OL9" / "README.md").read_text(encoding="utf-8")
        ubuntu_readme = (ROOT_DIR / "ubuntu" / "README.md").read_text(encoding="utf-8")

        self.assertIn("./docker/setup_docker.sh", docker_readme)
        self.assertIn("./OL9/setup.sh", ol9_readme)
        self.assertIn("./OL9/init_docker.sh", ol9_readme)
        self.assertIn("./ubuntu/setup.sh", ubuntu_readme)


if __name__ == "__main__":
    unittest.main()
