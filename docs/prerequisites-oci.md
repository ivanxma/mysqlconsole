# OCI Compute and Object Storage prerequisites

Complete these requirements before installing DBConsole on OCI Compute or configuring **Admin > Setup Object Storage**.

## Compute

- Create an Oracle Linux 9 or Ubuntu Compute instance.
- Use the image’s normal SSH user (`opc` for Oracle Linux, `ubuntu` for Ubuntu) and grant it `sudo` access.
- Allow inbound TCP `443` for HTTPS, or `80` for HTTP, in the instance subnet security list or NSG. Setup configures the host firewall, but it cannot alter OCI network rules.
- Ensure the instance can reach GitHub, Oracle package repositories, and OCI Object Storage over HTTPS.

## Test bucket

Create a dedicated test bucket in the same compartment as the instance, for example `dbconsole-test`. Record:

- region, such as `uk-london-1`;
- Object Storage namespace;
- bucket name (`dbconsole-test` in this example); and
- an optional prefix such as `dbconsole/`.

Do not use a production bucket for validation. The application can upload and list objects under the configured bucket and prefix.

## Instance Principal IAM

Create a dynamic group containing the DBConsole Compute instances. A compartment-based matching rule is commonly sufficient:

```text
instance.compartment.id = '<compute-compartment-ocid>'
```

Create a policy in the bucket’s compartment, replacing the names and compartment with your own values:

```text
Allow dynamic-group dbconsole-instances to inspect buckets in compartment dbconsole
Allow dynamic-group dbconsole-instances to manage objects in compartment dbconsole where target.bucket.name = 'dbconsole-test'
```

If the Compute instance and bucket are in different compartments, write the bucket permissions in the bucket compartment and ensure the dynamic-group rule matches the instance compartment. Allow a few minutes for IAM policy propagation before testing.

## Configure DBConsole

After signing in through `local-admin-profile`, open **Admin > Setup Object Storage** and enter the recorded region, namespace, bucket name, and optional prefix. Use the page’s validation/upload flow with a harmless test CSV first.

DBConsole does not accept OCI API-key configuration files or private keys; it uses the Compute instance’s principal. Consequently, this Object Storage setup is not available from the Mac Docker installation.
