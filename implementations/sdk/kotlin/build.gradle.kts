plugins {
    kotlin("jvm") version "2.4.10"
    `maven-publish`
    `signing`
}

group = "systems.spear"
version = "1.0.2"

repositories { mavenCentral() }

dependencies {
    implementation("com.networknt:json-schema-validator:3.0.6")
    // Jackson 3 (tools.jackson) is used for both the SDK's own envelope/$id
    // parsing and networknt 3.x. 3.2.1 fixes CVE-2026-54512, CVE-2026-54513,
    // and CVE-2026-59889 (@JsonView bypass for @JsonUnwrapped containers);
    // the old Jackson 2 pins are gone with the 1.5.9 line.
    implementation("tools.jackson.core:jackson-databind:3.2.2")
    testImplementation(kotlin("test"))
}

kotlin { jvmToolchain(17) }

java {
    withSourcesJar()
    withJavadocJar()
}

publishing {
    publications {
        create<MavenPublication>("mavenJava") {
            artifactId = "lcp-sdk-kotlin"
            from(components["java"])
            pom {
                name.set("LCP Kotlin SDK")
                description.set("LCP Lead Context Protocol SDK for Kotlin")
                url.set("https://github.com/SpearSystems/LCP")
                licenses { license { name.set("Apache-2.0"); url.set("https://www.apache.org/licenses/LICENSE-2.0") } }
                developers {
                    developer {
                        id.set("spearsystems")
                        name.set("Spear Systems")
                        organization.set("Spear Systems")
                        organizationUrl.set("https://spear.systems")
                    }
                }
                scm {
                    connection.set("scm:git:https://github.com/SpearSystems/LCP.git")
                    developerConnection.set("scm:git:ssh://git@github.com/SpearSystems/LCP.git")
                    url.set("https://github.com/SpearSystems/LCP")
                }
            }
        }
    }
    repositories {
        maven {
            name = "release"
            url = uri(System.getenv("MAVEN_REPOSITORY_URL") ?: layout.buildDirectory.dir("repository"))
            credentials {
                username = System.getenv("MAVEN_USERNAME")
                password = System.getenv("MAVEN_PASSWORD")
            }
        }
    }
}

val publishingRequested = gradle.startParameter.taskNames.any { task ->
    task.substringAfterLast(":").startsWith("publish")
}

if (publishingRequested) {
    val signingKey = System.getenv("MAVEN_GPG_PRIVATE_KEY")
        ?: throw GradleException("MAVEN_GPG_PRIVATE_KEY is required for Maven publication")
    val signingPassphrase = System.getenv("MAVEN_GPG_PASSPHRASE")
        ?: throw GradleException("MAVEN_GPG_PASSPHRASE is required for Maven publication")

    signing {
        useInMemoryPgpKeys(signingKey, signingPassphrase)
        sign(publishing.publications["mavenJava"])
    }
}
