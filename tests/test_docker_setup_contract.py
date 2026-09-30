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
        self.assertEqual(compose.count("- dbconsole-mysql-socket:/var/run/mysqld"), 2)
        self.assertIn("dbconsole-mysql-socket:", compose)

    def test_entrypoint_creates_a_socket_backed_local_admin_profile(self):
        entrypoint = (DOCKER_DIR / "docker-entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn('"socket_enabled": True', entrypoint)
        self.assertIn('"socket_path": os.environ.get("DBCONSOLE_LOCAL_MYSQL_SOCKET", "/var/run/mysqld/mysqld.sock")', entrypoint)

    def test_setup_script_verifies_the_socket_profile_after_recreation(self):
        setup_script = (DOCKER_DIR / "setup_docker.sh").read_text(encoding="utf-8")

        self.assertIn("compose pull mysql", setup_script)
        self.assertIn("socket_path.is_socket()", setup_script)
        self.assertIn("The socket-backed local-admin-profile was not created.", setup_script)

    def test_deployment_assets_are_grouped_by_target(self):
        for path in (
            DOCKER_DIR / "Dockerfile",
            DOCKER_DIR / "compose.yaml",
            DOCKER_DIR / "README.md",
            DOCKER_DIR / "setup_docker.sh",
            ROOT_DIR / "OL9" / "README.md",
            ROOT_DIR / "OL9" / "setup.sh",
            ROOT_DIR / "OL9" / "oci_compute_init.sh",
            ROOT_DIR / "ubuntu" / "README.md",
            ROOT_DIR / "ubuntu" / "setup.sh",
            ROOT_DIR / "ubuntu" / "oci_compute_init.sh",
        ):
            self.assertTrue(path.is_file(), path)

    def test_each_target_readme_points_to_its_own_setup_entry_point(self):
        docker_readme = (DOCKER_DIR / "README.md").read_text(encoding="utf-8")
        ol9_readme = (ROOT_DIR / "OL9" / "README.md").read_text(encoding="utf-8")
        ubuntu_readme = (ROOT_DIR / "ubuntu" / "README.md").read_text(encoding="utf-8")

        self.assertIn("./docker/setup_docker.sh", docker_readme)
        self.assertIn("./OL9/setup.sh", ol9_readme)
        self.assertIn("./ubuntu/setup.sh", ubuntu_readme)


if __name__ == "__main__":
    unittest.main()
