## Google Cloud Build files

The `containers.yml` and `debs.yml` files are
[Google Cloud Build build configurations](https://cloud.google.com/cloud-build/docs/build-config)
for building and publishing the Spinnaker microservice containers and Debian
packages.

In order to use them, there must be a `save_cache` and `restore_cache` image in
the Google Container Registry of the project in which the configurations are
executed. You can create those images with the `build-steps.yml` file:

```
gcloud builds submit --config=build-steps.yml --project=spinnaker-community .
```

The source for these images is the
[cloud-builders-community repository](https://github.com/GoogleCloudPlatform/cloud-builders-community/tree/master/cache).
